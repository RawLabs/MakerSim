"""Durable, part-scoped sessions shared by the browser and local CAD agents.

SQLite transactions serialize checkpoints across HTTP, CLI and MCP processes.
CAD remains the connected client's responsibility; a step lease authorizes one
operation, and an interruption prevents the next operation from starting.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .geometry import read_stl
from .local import AgentError, MAX_STL_BYTES, get_result, read_bytes, read_json, run_test, write_json

ROOT = Path(__file__).resolve().parents[1]
Mode = Literal['interactive', 'auto', 'teaching']
Phase = Literal['plan', 'do', 'check', 'act']


class Budgets(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    max_iterations: int = Field(default=4, ge=1, le=1000)
    max_seconds: float = Field(default=1800, gt=0, le=86400)
    max_tokens: int | None = Field(default=None, ge=1, le=10_000_000)


class SessionSpec(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project: str = Field(default='My project', min_length=1, max_length=200)
    part: str = Field(default='New part', min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=5000)
    source: Literal['selected', 'copy', 'scratch'] = 'copy'
    mode: Mode = 'interactive'
    pause_checkpoints: bool = False
    notify_when_done: bool = True
    stop_on_criteria: bool = True
    budgets: Budgets = Field(default_factory=Budgets)


def validate(model, value):
    try:
        return model.model_validate(value).model_dump(mode='json')
    except ValidationError as error:
        raise AgentError('invalid_session', 'Check the session settings.',
                         [{key: item[key] for key in ('loc', 'msg', 'type')} for item in error.errors()]) from error


def encode(value):
    try:
        return json.dumps(value, allow_nan=False, separators=(',', ':'))
    except (ValueError, TypeError) as error:
        raise AgentError('invalid_session', 'Session data must be finite JSON.') from error


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    def __init__(self, root=None):
        self.root = Path(root or os.getenv('MAKERSIM_SESSION_DIR', ROOT / '.makersim/sessions')).expanduser().resolve()

    @contextmanager
    def transaction(self):
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.root / 'sessions.sqlite3', timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, snapshot TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    session_id TEXT NOT NULL, sequence INTEGER NOT NULL, event TEXT NOT NULL,
                    PRIMARY KEY (session_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS runtime (
                    name TEXT PRIMARY KEY, value TEXT NOT NULL
                );
            ''')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _get(self, db, session_id, owner=None):
        row = db.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone()
        if row is None or (owner is not None and row['owner'] != owner):
            raise AgentError('session_not_found', 'This session is not in your workspace.')
        session = json.loads(row['snapshot'])
        session['viewer_epoch'] = self._viewer_epoch(db)
        return session

    def _viewer_epoch(self, db):
        row = db.execute('SELECT value FROM runtime WHERE name=?', ('viewer_epoch',)).fetchone()
        return row['value'] if row else 'local-cli'

    def viewer_started(self):
        """A reopened service needs fresh agent evidence, without changing saved work or permissions."""
        with self.transaction() as db:
            db.execute('INSERT OR REPLACE INTO runtime VALUES (?, ?)', ('viewer_epoch', uuid.uuid4().hex))

    def _agent_live(self, session):
        run = session.get('agent_run')
        return bool(run and run['state'] != 'ended' and run['viewer_epoch'] == session['viewer_epoch']
                    and time.time() - run['last_seen_at'] < 60)

    def _touch_agent(self, session, run_id):
        run = session.get('agent_run')
        if not run or run['id'] != run_id or run['state'] == 'ended':
            raise AgentError('stale_agent', 'This agent connection has ended or been replaced. Reconnect before reporting work.')
        run.update(last_seen_at=time.time(), viewer_epoch=session['viewer_epoch'])

    def connect_agent(self, session_id, agent_name, supports_controls=False):
        """Announce a named client; connection alone never authorizes design work."""
        if not isinstance(agent_name, str) or not agent_name.strip() or len(agent_name) > 120 or not isinstance(supports_controls, bool):
            raise AgentError('invalid_session', 'Name your agent in 1–120 characters and declare whether it supports design controls.')
        with self.transaction() as db:
            session = self._get(db, session_id)
            if self._agent_live(session):
                raise AgentError('agent_active', 'An agent is already connected. End that connection before connecting another.')
            session['agent_run'] = {'id': uuid.uuid4().hex, 'name': agent_name.strip(),
                'supports_controls': supports_controls, 'state': 'connected', 'last_seen_at': time.time(),
                'viewer_epoch': session['viewer_epoch']}
            self._event(db, session, 'agent_connected', f'{agent_name.strip()} connected. Waiting for a human instruction.',
                        {'supports_controls': supports_controls})
        return {'run_id': session['agent_run']['id'], 'session': self.describe(session)}

    def agent_signal(self, session_id, run_id, action='heartbeat', seen_sequence=None):
        if action not in ('heartbeat', 'disconnect'):
            raise AgentError('invalid_session', 'Use heartbeat or disconnect.')
        with self.transaction() as db:
            session = self._get(db, session_id)
            self._touch_agent(session, run_id)
            if action == 'disconnect':
                if seen_sequence is None and session['agent_run']['supports_controls']:
                    raise AgentError('input_check_required', 'Read and handle the session events before disconnecting. Supply the last event cursor you checked.')
                if seen_sequence is not None:
                    if isinstance(seen_sequence, bool) or not isinstance(seen_sequence, int) or not 0 <= seen_sequence <= session['sequence']:
                        raise AgentError('invalid_session', 'Use the last checked event cursor, within this session timeline.')
                    pending = []
                    for row in db.execute('SELECT event FROM events WHERE session_id=? AND sequence>? ORDER BY sequence',
                                          (session_id, seen_sequence)):
                        event = json.loads(row['event'])
                        if event['kind'] in ('intervention', 'feedback', 'continued', 'settings', 'recovered'):
                            pending.append({'sequence': event['sequence'], 'kind': event['kind']})
                            if len(pending) == 10:
                                break
                    if pending:
                        raise AgentError('pending_input', 'New human input arrived. Read and handle it before disconnecting.', pending)
                session['agent_run']['state'] = 'ended'
            self._event(db, session, 'agent_disconnected' if action == 'disconnect' else 'agent_heartbeat',
                        'Agent disconnected; design history remains saved.' if action == 'disconnect' else 'Agent connection refreshed.')
        return self.describe(session)

    def authorize_run(self, session_id, run_id, human_instruction, action='start'):
        """Trusted integrations relay an explicit human start/resume; the model must never invent it."""
        if action not in ('start', 'resume') or not isinstance(human_instruction, str) or not human_instruction.strip() or len(human_instruction) > 5000:
            raise AgentError('invalid_session', 'Relay the human’s explicit start or resume instruction in 1–5000 characters.')
        with self.transaction() as db:
            session = self._get(db, session_id)
            self._touch_agent(session, run_id)
            run = session['agent_run']
            if action == 'start':
                if run['state'] != 'connected':
                    raise AgentError('agent_active', 'This run already started. A live pause needs an explicit human resume.')
                # Explicit new-run authorization supersedes an unfinished previous connection;
                # late exports can no longer overwrite this run. History and artifacts stay intact.
                abandoned = session['active_operation']
                session['active_operation'] = None
                if abandoned:
                    self._event(db, session, 'recovered', 'New human-authorized run superseded an unfinished old step.',
                                {'abandoned_operation': abandoned})
            elif run['state'] != 'working':
                raise AgentError('invalid_session', 'Start this connected run before resuming it.')
            self._allow_work(db, session, human_instruction)
            run['state'] = 'working'
            self._event(db, session, 'run_started' if action == 'start' else 'continued', human_instruction,
                        {'human_instruction': human_instruction, 'run_id': run_id}, 'plan')
        return self.describe(session)

    def _allow_work(self, db, session, message):
        if session['active_operation']:
            raise AgentError('operation_active', 'The current step is still finishing. Resume after it stops.')
        reason = self._budget_reason(session)
        if reason:
            raise AgentError('budget_reached', reason + ' Increase the work limits or start a separate copy.')
        session['status'], session['pause_reason'], session['activity'] = 'running', None, None

    def _event(self, db, session, kind, message, data=None, phase=None):
        session['viewer_epoch'] = self._viewer_epoch(db)
        session['sequence'] += 1
        session['updated_at'] = timestamp()
        event = {'sequence': session['sequence'], 'kind': kind, 'message': message,
                 'data': data or {}, 'phase': phase, 'created_at': session['updated_at'],
                 'state': json.loads(encode(session))}
        db.execute('INSERT INTO events VALUES (?, ?, ?)', (session['id'], event['sequence'], encode(event)))
        db.execute('UPDATE sessions SET snapshot=? WHERE id=?', (encode(session), session['id']))
        return event

    def create(self, spec, owner='local-agent'):
        settings = validate(SessionSpec, spec)
        if not settings['objective'].strip():
            raise AgentError('invalid_session', 'Describe the engineering objective.')
        session = {**settings, 'id': uuid.uuid4().hex, 'project_id': uuid.uuid4().hex,
                   'part_id': uuid.uuid4().hex, 'parent': None, 'status': 'paused',
                   'pause_reason': 'Work saved. Connect your chosen agent when you want to work on it.',
                   'agent_run': None,
                   'active_operation': None, 'activity': None, 'revision': None, 'report': None, 'last_report': None, 'sequence': 0,
                   'usage': {'iterations': 0, 'seconds': 0, 'tokens': 0}, 'created_at': timestamp()}
        with self.transaction() as db:
            db.execute('INSERT INTO sessions VALUES (?, ?, ?)', (session['id'], owner, encode(session)))
            self._event(db, session, 'created', session['objective'], phase='plan')
        return self.describe(session)

    def describe(self, session):
        return {**session, 'workspace_path': str(self.root / session['id']),
                'token_accounting': 'Reported by the connected agent; not a provider spending cap.'}

    def get(self, session_id, owner=None):
        with self.transaction() as db:
            return self.describe(self._get(db, session_id, owner))

    def connect(self, session_id, owner):
        """Explicitly attach a locally created CLI/MCP session to one browser."""
        with self.transaction() as db:
            session = self._get(db, session_id)
            current = db.execute('SELECT owner FROM sessions WHERE id=?', (session_id,)).fetchone()['owner']
            if current not in ('local-agent', owner):
                raise AgentError('session_not_found', 'This session is already attached to another workspace.')
            db.execute('UPDATE sessions SET owner=? WHERE id=?', (owner, session_id))
        return self.describe(session)

    def list(self, owner):
        with self.transaction() as db:
            epoch = self._viewer_epoch(db)
            return sorted([self.describe({**json.loads(row['snapshot']), 'viewer_epoch': epoch}) for row in
                           db.execute('SELECT snapshot FROM sessions WHERE owner=?', (owner,))],
                          key=lambda session: session['updated_at'], reverse=True)

    def events(self, session_id, after=0, limit=100, owner=None):
        if not isinstance(after, int) or after < 0 or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise AgentError('invalid_session', 'Use a nonnegative cursor and an event limit of 1–500.')
        with self.transaction() as db:
            session = self._get(db, session_id, owner)
            events = [json.loads(row['event']) for row in db.execute(
                'SELECT event FROM events WHERE session_id=? AND sequence>? ORDER BY sequence LIMIT ?',
                (session_id, after, limit))]
        return {'session': self.describe(session), 'events': events,
                'next_after': events[-1]['sequence'] if events else after}

    def context(self, session_id, query='', limit=30):
        """Retrieve only this part's branch history, including its frozen parent prefix."""
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise AgentError('invalid_session', 'Context limits must be 1–100.')
        with self.transaction() as db:
            session = self._get(db, session_id)
            rows = db.execute('SELECT event FROM events WHERE session_id=? ORDER BY sequence DESC', (session_id,))
            terms = query.lower().split()
            matches = []
            for row in rows:
                event = json.loads(row['event'])
                if event['kind'] in ('intervention', 'lesson', 'plan', 'prediction', 'explanation', 'decision',
                                     'result', 'step_failed', 'checkpoint', 'branched', 'feedback', 'run_started', 'continued'):
                    content = (event['message'] + ' ' + encode(event['data'])).lower()
                    if not terms or all(term in content for term in terms):
                        matches.append({key: value for key, value in event.items() if key != 'state'})
                if len(matches) >= limit:
                    break
        return {'session': self.describe(session), 'memory': list(reversed(matches)),
                'scope': {'project_id': session['project_id'], 'part_id': session['part_id'], 'branch_id': session_id}}

    def control(self, session_id, action, message='', settings=None, owner=None):
        with self.transaction() as db:
            session = self._get(db, session_id, owner)
            if owner is not None and action in ('pause', 'interrupt', 'continue', 'recover'):
                if not self._agent_live(session) or not session['agent_run']['supports_controls']:
                    raise AgentError('controls_unavailable', 'Use your agent app to control this work. No connected agent has enabled window controls.')
            if action in ('pause', 'interrupt'):
                if not isinstance(message, str) or len(message) > 5000:
                    raise AgentError('invalid_session', 'Interventions must contain at most 5000 characters.')
                session['status'] = 'paused'
                session['activity'] = None
                session['pause_reason'] = message or 'You paused the work.'
                self._event(db, session, 'intervention', session['pause_reason'], phase='plan')
            elif action == 'continue':
                run = session.get('agent_run')
                if run and run['state'] != 'working':
                    raise AgentError('invalid_session', 'Start a new run from your agent app first.')
                self._allow_work(db, session, message)
                self._event(db, session, 'continued', 'Human selected Continue.')
            elif action == 'feedback':
                if not isinstance(message, str) or not message.strip() or len(message) > 5000:
                    raise AgentError('invalid_session', 'Write your question or direction in 1–5000 characters.')
                self._event(db, session, 'feedback', message, {'delivery': 'saved_for_agent'}, 'plan')
            elif action == 'settings':
                values = settings or {}
                if set(values) - {'mode', 'pause_checkpoints', 'notify_when_done', 'stop_on_criteria', 'budgets', 'objective'}:
                    raise AgentError('invalid_session', 'Only mode, checkpoints, notification, stopping criteria, objective and budgets can change.')
                merged = validate(SessionSpec, {key: values.get(key, session[key]) for key in SessionSpec.model_fields})
                if not merged['objective'].strip():
                    raise AgentError('invalid_session', 'Describe the engineering objective.')
                session.update(merged)
                if 'objective' in values:
                    session['status'], session['pause_reason'] = 'paused', 'Objective changed. Review and Continue.'
                    session['activity'] = None
                self._event(db, session, 'settings', 'Session settings updated.', values, phase='plan')
            elif action == 'recover':
                abandoned = session['active_operation']
                session['active_operation'] = None
                session['activity'] = None
                session['status'], session['pause_reason'] = 'paused', 'Unfinished step reset. Review the saved work before Continue.'
                self._event(db, session, 'recovered', session['pause_reason'], {'abandoned_operation': abandoned})
            else:
                raise AgentError('invalid_session', 'Unknown session control.')
        return self.describe(session)

    def _budget_reason(self, session):
        usage, budget = session['usage'], session['budgets']
        if usage['iterations'] >= budget['max_iterations']:
            return 'Maximum number of simulation runs reached.'
        if usage['seconds'] >= budget['max_seconds']:
            return 'Build and test time limit reached.'
        if budget['max_tokens'] is not None and usage['tokens'] >= budget['max_tokens']:
            return 'AI usage limit reached, based on the agent’s reported tokens.'
        return None

    def report_activity(self, session_id, kind, message, operation_id=None, run_id=None):
        """Report text work or refresh one active operation; never grant permission to edit.

        Clients refresh long activities at least every 30 seconds. The browser treats
        60 seconds without an update as uncertainty, not proof that work stopped.
        """
        if kind not in ('thinking', 'explaining', 'waiting', 'working'):
            raise AgentError('invalid_session', 'Report thinking, explaining, waiting or working.')
        if not isinstance(message, str) or not message.strip() or len(message) > 5000:
            raise AgentError('invalid_session', 'Describe the current activity in 1–5000 characters.')
        with self.transaction() as db:
            session = self._get(db, session_id)
            if session.get('agent_run') or run_id is not None:
                self._touch_agent(session, run_id)
            operation = session['active_operation']
            now = time.time()
            if kind == 'working':
                if not operation or operation['id'] != operation_id:
                    raise AgentError('stale_operation', 'Only the active operation can report progress.')
                heartbeat = operation.get('progress') == message
                operation.update(updated_at=now, progress=message)
            else:
                if operation_id is not None:
                    raise AgentError('invalid_session', 'Use an operation ID only when reporting working progress.')
                if operation:
                    raise AgentError('operation_active', 'Report working progress until the active step finishes.')
                if session['status'] == 'review':
                    raise AgentError('invalid_session', 'This session is ready for review; no activity is running.')
                previous = session.get('activity') or {}
                heartbeat = previous.get('kind') == kind and previous.get('message') == message
                session['activity'] = {'kind': kind, 'message': message, 'updated_at': now,
                                       'started_at': previous['started_at'] if heartbeat else now}
            event = self._event(db, session, 'activity', message, {'kind': kind, 'heartbeat': heartbeat})
        return event

    def publish(self, session_id, kind, message, data=None, phase=None, run_id=None):
        if kind not in ('plan', 'prediction', 'explanation', 'decision', 'lesson', 'checkpoint'):
            raise AgentError('invalid_session', 'Use a plan, prediction, explanation, decision, lesson or checkpoint event.')
        if not isinstance(message, str) or not message.strip() or len(message) > 5000:
            raise AgentError('invalid_session', 'Event messages must contain 1–5000 characters.')
        if phase not in (None, 'plan', 'do', 'check', 'act'):
            raise AgentError('invalid_session', 'Use a PDCA phase: plan, do, check or act.')
        data = data or {}
        if not isinstance(data, dict) or len(encode(data)) > 16000:
            raise AgentError('invalid_session', 'Event details must be a JSON object of at most 16000 characters.')
        if kind == 'lesson' and data.get('evidence_type') not in ('hypothesis', 'simulation', 'human_observation', 'physical_test'):
            raise AgentError('invalid_session', 'Lessons need an evidence_type: hypothesis, simulation, human_observation or physical_test.')
        with self.transaction() as db:
            session = self._get(db, session_id)
            if session.get('agent_run') or run_id is not None:
                self._touch_agent(session, run_id)
            if kind == 'checkpoint' and session['pause_checkpoints'] and session['status'] == 'running':
                session['status'], session['pause_reason'] = 'paused', message
                session['activity'] = None
            if kind == 'explanation' and (session.get('activity') or {}).get('kind') == 'explaining':
                session['activity'] = None
            event = self._event(db, session, kind, message, data, phase)
        return event

    def begin_step(self, session_id, description, phase='do', operation='cad', run_id=None):
        if operation not in ('cad', 'simulation') or phase not in ('plan', 'do', 'check', 'act'):
            raise AgentError('invalid_session', 'Use a CAD/simulation operation and a PDCA phase.')
        if not isinstance(description, str) or not description.strip() or len(description) > 5000:
            raise AgentError('invalid_session', 'Describe this operation in 1–5000 characters.')
        with self.transaction() as db:
            session = self._get(db, session_id)
            run = session.get('agent_run')
            if run:
                self._touch_agent(session, run_id)
                if run['state'] != 'working':
                    raise AgentError('session_paused', 'Connection alone does not start work. Wait for an explicit human start instruction.')
            if session['status'] != 'running':
                raise AgentError('session_paused', 'Wait for an explicit human start or resume. Explanations may still be published.')
            if session['active_operation']:
                raise AgentError('operation_active', 'Finish the current operation before beginning another.')
            reason = self._budget_reason(session)
            if reason:
                session['status'], session['pause_reason'] = 'review', reason
                session['activity'] = None
                self._event(db, session, 'review_ready', reason, phase='act')
                # Commit the stopping decision before returning the error.
                blocked = reason
            else:
                blocked = None
                now = time.time()
                session['activity'] = None
                session['active_operation'] = {'id': uuid.uuid4().hex, 'description': description,
                                               'phase': phase, 'kind': operation, 'started_at': now, 'updated_at': now,
                                               'run_id': run_id}
                self._event(db, session, 'step_started', description, phase=phase)
        if blocked:
            raise AgentError('budget_reached', blocked)
        return {'operation_id': session['active_operation']['id'], 'session': self.describe(session)}

    def _save_geometry(self, session_id, data, unit, revision, cad_reference=None):
        if unit not in ('mm', 'inch') or not isinstance(revision, str) or not revision.strip() or len(revision) > 200:
            raise AgentError('invalid_session', 'Geometry needs mm/inch units and a revision label of 1–200 characters.')
        if len(data) > MAX_STL_BYTES:
            raise AgentError('file_too_large', 'Session STLs can be up to 64 MB.')
        try:
            mesh = read_stl(data, unit)
        except ValueError as error:
            raise AgentError('invalid_geometry', str(error)) from error
        folder = self.root / session_id
        folder.mkdir(exist_ok=True)
        name = uuid.uuid4().hex + '.stl'
        (folder / name).write_bytes(data)
        return {'artifact': name, 'unit': unit, 'label': revision, 'cad_reference': cad_reference,
                'sha256': hashlib.sha256(data).hexdigest(), 'source_center_mm': mesh.source_center_mm.tolist()}

    def attach_baseline(self, session_id, data, unit='mm', revision='R0', owner=None):
        with self.transaction() as db:
            session = self._get(db, session_id, owner)
            if session['revision'] is not None or session['active_operation'] or session['status'] != 'paused':
                raise AgentError('invalid_session', 'Attach the starting part only once, before work begins.')
            session['revision'] = self._save_geometry(session_id, data, unit, revision)
            self._event(db, session, 'geometry', 'Starting part preserved.', {'revision': session['revision']}, 'do')
        return self.describe(session)

    def finish_step(self, session_id, operation_id, explanation, stl_path=None, unit='mm', revision=None,
                    cad_reference=None, tokens_used=0, error=None, report=None):
        if not isinstance(tokens_used, int) or isinstance(tokens_used, bool) or tokens_used < 0:
            raise AgentError('invalid_session', 'Report a nonnegative integer token count.')
        if not isinstance(explanation, str) or not explanation.strip() or len(explanation) > 5000:
            raise AgentError('invalid_session', 'Explain the outcome in 1–5000 characters.')
        with self.transaction() as db:
            session = self._get(db, session_id)
            operation = session['active_operation']
            if not operation or operation['id'] != operation_id:
                raise AgentError('stale_operation', 'This operation is no longer active. Its result cannot update the session.')
            run = session.get('agent_run')
            if run and run['id'] == operation.get('run_id') and run['state'] != 'ended':
                self._touch_agent(session, run['id'])
            session['usage']['seconds'] += max(0, time.time() - operation['started_at'])
            session['usage']['tokens'] += tokens_used
            if operation['kind'] == 'simulation':
                session['usage']['iterations'] += 1
            if stl_path:
                session['revision'] = self._save_geometry(session_id, read_bytes(stl_path, MAX_STL_BYTES),
                                                          unit, revision or operation_id, cad_reference)
            session['active_operation'] = None
            session['activity'] = None
            if stl_path:
                session['report'] = None
                self._event(db, session, 'geometry', explanation, {'revision': session['revision']}, 'do')
            if report:
                session['report'] = report
                session['last_report'] = report
                self._event(db, session, 'result', explanation, {'report': report}, 'check')
            if error:
                session['status'], session['pause_reason'] = 'paused', str(error)
            self._event(db, session, 'step_failed' if error else 'step_finished', explanation,
                        {'error': str(error)} if error else {}, operation['phase'])
            reason = self._budget_reason(session)
            criteria_met = bool(report and report['criteria_status'] == 'met' and session['stop_on_criteria'])
            if session['status'] == 'running' and (reason or criteria_met or session['pause_checkpoints']):
                session['status'] = 'review' if reason or criteria_met else 'paused'
                session['pause_reason'] = reason or ('Saved simulation goals met. Review the design before choosing it.'
                                                    if criteria_met else 'Step finished. Review it and explicitly resume when ready.')
                self._event(db, session, 'review_ready' if reason or criteria_met else 'checkpoint', session['pause_reason'],
                            {'physical_validation': 'not_established', 'acceptance': 'human_review_required'} if criteria_met else {}, 'act')
        return self.describe(session)

    def complete(self, session_id, summary, run_id=None):
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 5000:
            raise AgentError('invalid_session', 'Provide a completion summary of 1–5000 characters.')
        with self.transaction() as db:
            session = self._get(db, session_id)
            if session.get('agent_run'):
                self._touch_agent(session, run_id)
            if session['active_operation'] or session['status'] != 'running':
                raise AgentError('session_paused', 'Finish the operation and wait for an explicit human resume before ending a session.')
            session['status'], session['pause_reason'] = 'review', 'Candidate ready for review.'
            session['activity'] = None
            self._event(db, session, 'review_ready', summary,
                        {'physical_validation': 'not_established', 'acceptance': 'human_review_required'}, 'act')
        return self.describe(session)

    def artifact(self, session_id, name, owner=None):
        with self.transaction() as db:
            self._get(db, session_id, owner)
            if not isinstance(name, str) or Path(name).name != name or name in ('.', '..'):
                raise AgentError('file_error', 'Choose an artifact from this session.')
            # Only files referenced by committed timeline entries can be served.
            allowed = set()
            for row in db.execute('SELECT event FROM events WHERE session_id=?', (session_id,)):
                snapshot = json.loads(row['event'])['state']
                if snapshot.get('revision'):
                    allowed.add(snapshot['revision']['artifact'])
                if snapshot.get('report'):
                    allowed.update(snapshot['report'].get(key) for key in ('artifact', 'test_artifact'))
            path = self.root / session_id / name
            if name not in allowed or not path.is_file() or path.resolve().parent != (self.root / session_id).resolve():
                raise AgentError('file_error', 'This artifact is not in the session timeline.')
        return path

    def fork(self, session_id, sequence=None, objective=None, owner=None):
        with self.transaction() as db:
            original = self._get(db, session_id, owner)
            sequence = original['sequence'] if sequence is None else sequence
            rows = list(db.execute('SELECT event FROM events WHERE session_id=? AND sequence<=? ORDER BY sequence',
                                   (session_id, sequence)))
            if not rows or json.loads(rows[-1]['event'])['sequence'] != sequence:
                raise AgentError('invalid_session', 'Select an existing timeline event to branch from.')
            session = json.loads(rows[-1]['event'])['state']
            session.update(id=uuid.uuid4().hex, parent={'session_id': session_id, 'sequence': sequence},
                           active_operation=None, activity=None, agent_run=None, status='paused', source='copy', created_at=timestamp(),
                           pause_reason='Separate copy saved. Connect your chosen agent when ready.',
                           usage={'iterations': 0, 'seconds': 0, 'tokens': 0})
            if objective is not None:
                session['objective'] = validate(SessionSpec, {key: objective if key == 'objective' else session[key]
                                                              for key in SessionSpec.model_fields})['objective']
            new_owner = db.execute('SELECT owner FROM sessions WHERE id=?', (session_id,)).fetchone()['owner']
            db.execute('INSERT INTO sessions VALUES (?, ?, ?)', (session['id'], new_owner, encode(session)))
            folder = self.root / session['id']
            folder.mkdir(exist_ok=True)
            copied = set()
            for row in rows:
                event = json.loads(row['event'])
                event.setdefault('origin_session', session_id)
                snapshot = event['state']
                names = [snapshot['revision']['artifact']] if snapshot.get('revision') else []
                if snapshot.get('report'):
                    names += [snapshot['report'][key] for key in ('artifact', 'test_artifact')]
                for name in names:
                    if name not in copied:
                        shutil.copyfile(self.root / session_id / name, folder / name)
                        copied.add(name)
                db.execute('INSERT INTO events VALUES (?, ?, ?)', (session['id'], event['sequence'], encode(event)))
            self._event(db, session, 'branched', session['objective'], {'parent': session['parent']}, 'plan')
        return self.describe(session)

    def run_simulation(self, session_id, test_path, stl_path=None, revision=None, resolution=28, max_cells=4800, run_id=None):
        """Use the existing solver, with immutable inputs and an intervention checkpoint."""
        lease = self.begin_step(session_id, 'Test the current revision against the saved load cases.', 'check', 'simulation', run_id)
        operation_id, session = lease['operation_id'], lease['session']
        folder = self.root / session_id
        folder.mkdir(exist_ok=True)
        report_name, test_name = operation_id + '.json', operation_id + '-test.json'
        try:
            spec = read_json(test_path, 1024 * 1024)
            write_json(folder / test_name, spec)
            if stl_path is not None:
                # Preserve the exact tested source, even when exported outside the session.
                geometry = self._save_geometry(session_id, read_bytes(stl_path, MAX_STL_BYTES),
                                               spec.get('stl_unit', 'mm'), revision or operation_id)
                source = folder / geometry['artifact']
            elif session['revision']:
                geometry = session['revision']
                source = self.artifact(session_id, geometry['artifact'])
                if geometry['unit'] != spec.get('stl_unit', 'mm'):
                    raise AgentError('invalid_session', 'The test STL units must match the saved geometry units.')
            else:
                raise AgentError('invalid_session', 'Publish a CAD revision before simulating.')
            previous = session.get('last_report')
            parent_path = self.artifact(session_id, previous['artifact']) if previous else None
            report = run_test(source, spec, folder / report_name, True, revision or geometry['label'],
                              parent_report=parent_path, goal=session['objective'], branch=session_id,
                              resolution=resolution, max_cells=max_cells)
            report_meta = {'artifact': report_name, 'test_artifact': test_name,
                           'criteria_status': report['criteria_status'],
                           'metrics': [{'name': case['name'], 'max_displacement_mm': case['max_displacement_mm'],
                                        'peak_surface_stress_mpa': case['peak_surface_stress_mpa']} for case in report['load_cases']]}
            updated = self.finish_step(session_id, operation_id, 'Simulation complete. Review the results and modeling assumptions.',
                                       stl_path=source, unit=geometry['unit'], revision=geometry['label'],
                                       cad_reference=geometry.get('cad_reference'), report=report_meta)
            return {'session': updated, 'report': get_result(folder / report_name, 'metrics')}
        except Exception as error:
            # A human recovery invalidates this lease; never resurrect an abandoned operation.
            try:
                self.finish_step(session_id, operation_id, 'Simulation could not complete.', error=str(error))
            except AgentError as finish_error:
                if finish_error.code != 'stale_operation':
                    raise
            if isinstance(error, AgentError):
                raise
            raise AgentError('simulation_failed', str(error)) from error


def session_store():
    return SessionStore()
