"""Session continuity, interruption, isolation and real-solver integration."""
from pathlib import Path

import pytest

from backend.local import AgentError
from backend.sessions import SessionStore

ROOT = Path(__file__).resolve().parents[1]
STL = ROOT / 'backend/data/backpack-bracket.stl'
TEST = ROOT / 'backend/data/bracket-test.json'


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / 'sessions')


def create(store, **settings):
    return store.create({'objective': 'Explain and test a rib.', **settings})['id']


def code(expected, function, *args, **kwargs):
    with pytest.raises(AgentError) as caught:
        function(*args, **kwargs)
    assert caught.value.code == expected


def test_every_interruption_requires_human_continue_and_preserves_inflight_work(store):
    session_id = create(store)
    code('session_paused', store.begin_step, session_id, 'Draw a rib.')
    store.control(session_id, 'continue')
    lease = store.begin_step(session_id, 'Draw a rib.')
    code('operation_active', store.begin_step, session_id, 'Draw another rib.')
    store.control(session_id, 'interrupt', 'Why this thickness? Keep bolt clearance.')
    store.publish(session_id, 'explanation', 'This is a bending hypothesis; we still need to test it.')
    code('operation_active', store.control, session_id, 'continue')
    completed = store.finish_step(session_id, lease['operation_id'], 'Current edit preserved.',
                                 stl_path=STL, revision='R1', cad_reference='onshape:document/version/R1')
    assert completed['status'] == 'paused' and completed['active_operation'] is None
    assert completed['revision']['cad_reference'].endswith('/R1')
    code('session_paused', store.begin_step, session_id, 'Test another approach.')
    # A mode change and an answer cannot silently restart a paused session.
    store.control(session_id, 'settings', settings={'mode': 'auto'})
    assert store.get(session_id)['status'] == 'paused'
    store.control(session_id, 'continue')
    assert store.begin_step(session_id, 'Next explicit operation.')['operation_id']


def test_activity_progress_is_scoped_and_cannot_resume_or_resurrect_work(store, monkeypatch):
    import backend.sessions as sessions
    clock = [1000]
    monkeypatch.setattr(sessions.time, 'time', lambda: clock[0])
    session_id = create(store)
    answering = store.report_activity(session_id, 'explaining', 'Explaining the load path.')
    assert answering['state']['status'] == 'paused'
    code('session_paused', store.begin_step, session_id, 'Draw while answering.')
    store.publish(session_id, 'explanation', 'The rib connects the loaded arm to the mounting wall.')
    assert store.get(session_id)['activity'] is None
    store.control(session_id, 'continue')
    thinking = store.report_activity(session_id, 'thinking', 'Considering bolt access.')
    clock[0] += 30
    update = store.report_activity(session_id, 'thinking', 'Considering bolt access.')
    assert update['data']['heartbeat']
    assert update['state']['activity']['started_at'] == thinking['state']['activity']['started_at']
    assert update['state']['activity']['updated_at'] == clock[0]
    copied = store.fork(session_id)
    assert copied['activity'] is None and copied['status'] == 'paused'
    assert not store.context(copied['id'], 'Considering bolt access')['memory']  # Activity is not a design lesson.
    lease = store.begin_step(session_id, 'Draw the rib.')
    assert lease['session']['activity'] is None
    code('operation_active', store.report_activity, session_id, 'thinking', 'An unrelated task.')
    code('stale_operation', store.report_activity, session_id, 'working', 'Progress.', 'wrong-id')
    store.control(session_id, 'interrupt', 'Pause for discussion.')
    progress = store.report_activity(session_id, 'working', 'Saving the sketch.', lease['operation_id'])
    assert progress['state']['status'] == 'paused'
    assert progress['state']['active_operation']['updated_at'] == clock[0]
    store.finish_step(session_id, lease['operation_id'], 'Saved at the pause boundary.')
    code('stale_operation', store.report_activity, session_id, 'working', 'Late progress.', lease['operation_id'])
    assert store.get(session_id)['status'] == 'paused'
    # Existing persisted snapshots predate the optional activity field.
    with store.transaction() as db:
        old = store._get(db, session_id)
        old.pop('activity')
        db.execute('UPDATE sessions SET snapshot=? WHERE id=?', (sessions.encode(old), session_id))
    assert store.report_activity(session_id, 'waiting', 'Waiting for your Continue.')['state']['status'] == 'paused'


def test_persistence_and_event_cursors_across_store_instances(store):
    session_id = create(store)
    store.attach_baseline(session_id, STL.read_bytes())
    store.control(session_id, 'interrupt', 'Keep the original mounting spacing.')
    reloaded = SessionStore(store.root)
    assert reloaded.get(session_id)['status'] == 'paused'
    assert reloaded.get(session_id)['pause_reason'] == 'Keep the original mounting spacing.'
    first = reloaded.events(session_id, limit=2)
    remaining = reloaded.events(session_id, after=first['next_after'])
    assert [event['sequence'] for event in first['events'] + remaining['events']] == [1, 2, 3]
    assert reloaded.artifact(session_id, reloaded.get(session_id)['revision']['artifact']).read_bytes() == STL.read_bytes()


def test_named_agent_connection_does_not_start_work_and_live_pause_needs_human_resume(store):
    session_id = create(store)
    connected = store.connect_agent(session_id, 'My engineering agent', supports_controls=True)
    run_id = connected['run_id']
    code('session_paused', store.begin_step, session_id, 'Draw.', run_id=run_id)
    code('invalid_session', store.authorize_run, session_id, run_id, '', 'start')
    started = store.authorize_run(session_id, run_id, 'Start a rib on a copy of this bracket.')
    assert started['status'] == 'running' and started['agent_run']['state'] == 'working'
    code('agent_active', store.connect_agent, session_id, 'Another app')
    lease = store.begin_step(session_id, 'Draw a rib.', run_id=run_id)
    store.control(session_id, 'interrupt', 'Pause to explain this thickness.')
    store.finish_step(session_id, lease['operation_id'], 'Edit preserved.')
    store.report_activity(session_id, 'explaining', 'Answering the thickness question.', run_id=run_id)
    assert store.get(session_id)['status'] == 'paused'
    code('agent_active', store.authorize_run, session_id, run_id, 'A new direction is not a resume.', 'start')
    resumed = store.authorize_run(session_id, run_id, 'Continue the design work.', 'resume')
    assert resumed['status'] == 'running'
    assert store.context(session_id, 'Continue the design')['memory'][-1]['data']['human_instruction'] == 'Continue the design work.'
    branch = store.fork(session_id)
    assert branch['agent_run'] is None


def test_restart_restores_history_without_live_presence_or_old_run_authority(store):
    session_id = create(store)
    store.attach_baseline(session_id, STL.read_bytes())
    connected = store.connect_agent(session_id, 'First agent')
    store.authorize_run(session_id, connected['run_id'], 'Work on this saved part.')
    lease = store.begin_step(session_id, 'An unfinished edit.', run_id=connected['run_id'])
    store.control(session_id, 'interrupt', 'Keep the mounting dimensions.')
    before = store.get(session_id)
    store.viewer_started()
    restored = SessionStore(store.root).get(session_id)
    assert restored['agent_run']['viewer_epoch'] != restored['viewer_epoch']
    assert restored['sequence'] == before['sequence'] and restored['active_operation'] == before['active_operation']
    assert restored['pause_reason'] == before['pause_reason'] and restored['revision'] == before['revision']
    # A still-running background client can prove presence again without resuming a pause.
    present = store.agent_signal(session_id, connected['run_id'])
    assert present['agent_run']['viewer_epoch'] == present['viewer_epoch'] and present['status'] == 'paused'
    store.agent_signal(session_id, connected['run_id'], 'disconnect')
    replacement = store.connect_agent(session_id, 'Different agent app')
    assert store.get(session_id)['active_operation']['id'] == lease['operation_id']
    store.authorize_run(session_id, replacement['run_id'], 'Start a new run from the saved work.')
    code('stale_operation', store.finish_step, session_id, lease['operation_id'], 'Late old export.', stl_path=STL)
    code('stale_agent', store.agent_signal, session_id, connected['run_id'])
    assert store.context(session_id, 'mounting dimensions')['memory']


def test_agent_presence_timeout_disables_window_controls_without_resuming_or_erasing_work(store, monkeypatch):
    import backend.sessions as sessions
    clock = [1000]
    monkeypatch.setattr(sessions.time, 'time', lambda: clock[0])
    session = store.create({'objective': 'Explain a rib.'}, 'browser')
    connection = store.connect_agent(session['id'], 'Any compatible agent', True)
    store.authorize_run(session['id'], connection['run_id'], 'Start this design.')
    clock[0] += 61
    code('controls_unavailable', store.control, session['id'], 'pause', owner='browser')
    assert store.get(session['id'])['status'] == 'running'
    store.control(session['id'], 'feedback', 'Keep bolt access.', owner='browser')
    assert store.context(session['id'], 'bolt access')['memory']
    assert store.agent_signal(session['id'], connection['run_id'])['status'] == 'running'


@pytest.mark.parametrize('action', ['interrupt', 'feedback', 'continue', 'settings', 'recover'])
def test_disconnect_refuses_human_input_arriving_after_the_agent_checked_events(store, action):
    session_id = create(store)
    run_id = store.connect_agent(session_id, 'Interactive agent', True)['run_id']
    store.authorize_run(session_id, run_id, 'Work on this bracket.')
    if action == 'continue':
        store.control(session_id, 'interrupt', 'Review this before continuing.')
    last_checked = store.events(session_id)['next_after']
    # Reproduce the race: a window input arrives after the agent's read,
    # immediately before it attempts to close the connection.
    if action == 'settings':
        store.control(session_id, action, settings={'objective': 'Keep the mounting holes and add Victorian scrolls.'})
    else:
        store.control(session_id, action, 'Add exaggerated Victorian scrolls; keep the mounting holes.')
    before = store.get(session_id)
    code('pending_input', store.agent_signal, session_id, run_id, 'disconnect', seen_sequence=last_checked)
    assert store.get(session_id) == before
    inputs = store.events(session_id, after=last_checked)
    assert inputs['events']
    # Additional agent output after the checked cursor does not hide or
    # invalidate the already handled human input.
    store.publish(session_id, 'explanation', 'I read the latest window input.', run_id=run_id)
    ended = store.agent_signal(session_id, run_id, 'disconnect', seen_sequence=inputs['next_after'])
    assert ended['agent_run']['state'] == 'ended'
    assert ended['status'] == before['status']


def test_control_client_must_check_input_and_use_the_paginated_event_cursor_before_disconnect(store):
    session_id = create(store)
    run_id = store.connect_agent(session_id, 'Interactive agent', True)['run_id']
    store.authorize_run(session_id, run_id, 'Start the design.')
    store.control(session_id, 'interrupt', 'New design instruction.')
    store.control(session_id, 'continue')
    before = store.get(session_id)
    code('input_check_required', store.agent_signal, session_id, run_id, 'disconnect')
    for invalid in (-1, True, 1.5, before['sequence']+1):
        code('invalid_session', store.agent_signal, session_id, run_id, 'disconnect', seen_sequence=invalid)
    first_page = store.events(session_id, limit=1)
    assert first_page['session']['sequence'] > first_page['next_after']
    code('pending_input', store.agent_signal, session_id, run_id, 'disconnect', seen_sequence=first_page['next_after'])
    remaining = store.events(session_id, after=first_page['next_after'])
    ended = store.agent_signal(session_id, run_id, 'disconnect', seen_sequence=remaining['next_after'])
    assert ended['agent_run']['state'] == 'ended'
    assert store.context(session_id, 'New design instruction')['memory']


def test_exact_revision_branch_inherits_only_frozen_prefix_and_own_artifacts(store):
    session_id = create(store)
    baseline = store.attach_baseline(session_id, STL.read_bytes())
    before = store.publish(session_id, 'lesson', 'Ribs can help this load case.',
                           {'evidence_type': 'hypothesis', 'conditions': 'This bracket and this direction only.'}, 'act')
    store.control(session_id, 'interrupt', 'Later parent requirement: 9 mm clearance.')
    branch = store.fork(session_id, before['sequence'])
    assert branch['project_id'] == baseline['project_id'] and branch['part_id'] == baseline['part_id']
    assert branch['status'] == 'paused' and branch['active_operation'] is None
    assert branch['parent'] == {'session_id': session_id, 'sequence': before['sequence']}
    assert branch['revision'] == baseline['revision']
    parent_path = store.artifact(session_id, baseline['revision']['artifact'])
    branch_path = store.artifact(branch['id'], baseline['revision']['artifact'])
    assert parent_path != branch_path and parent_path.read_bytes() == branch_path.read_bytes()
    assert store.context(branch['id'], 'ribs')['memory']
    assert not store.context(branch['id'], '9 mm')['memory']
    store.control(branch['id'], 'interrupt', 'Branch-only constraint: 7 mm clearance.')
    assert not store.context(session_id, '7 mm')['memory']
    assert store.get(session_id)['pause_reason'].endswith('9 mm clearance.')


def test_no_context_or_artifact_access_between_parts_projects_and_owners(store):
    alice = store.create({'objective': 'Alice dimensions: 42 mm'}, 'alice')
    bob = store.create({'objective': 'Bob dimensions: 75 mm'}, 'bob')
    assert alice['project_id'] != bob['project_id'] and alice['part_id'] != bob['part_id']
    store.publish(alice['id'], 'decision', 'Keep the 42 mm dimension.')
    assert not store.context(bob['id'], '42 mm')['memory']
    code('session_not_found', store.get, alice['id'], 'bob')
    code('session_not_found', store.events, alice['id'], owner='bob')
    code('session_not_found', store.fork, alice['id'], owner='bob')
    assert len(store.list('alice')) == 1
    baseline = store.attach_baseline(alice['id'], STL.read_bytes(), owner='alice')
    code('session_not_found', store.artifact, alice['id'], baseline['revision']['artifact'], 'bob')
    code('file_error', store.artifact, alice['id'], '../sessions.sqlite3', 'alice')
    code('file_error', store.artifact, bob['id'], baseline['revision']['artifact'], 'bob')


def test_checkpoint_toggle_completion_and_recovery(store):
    session_id = create(store, pause_checkpoints=True)
    store.control(session_id, 'continue')
    lease = store.begin_step(session_id, 'Compare rib positions.')
    assert store.finish_step(session_id, lease['operation_id'], 'Compare before the next edit.')['status'] == 'paused'
    store.control(session_id, 'continue')
    store.publish(session_id, 'checkpoint', 'What did we predict?', phase='check')
    assert store.get(session_id)['status'] == 'paused'
    code('session_paused', store.complete, session_id, 'Ready for review.')
    store.control(session_id, 'continue')
    completed = store.complete(session_id, 'Numerical candidate ready; fit still needs checking.')
    assert completed['status'] == 'review'
    last = store.events(session_id)['events'][-1]
    assert last['data']['physical_validation'] == 'not_established'
    store.control(session_id, 'continue')
    abandoned = store.begin_step(session_id, 'Client disconnects during an edit.')
    recovered = store.control(session_id, 'recover')
    assert recovered['status'] == 'paused' and recovered['active_operation'] is None
    code('stale_operation', store.finish_step, session_id, abandoned['operation_id'], 'Late result.')


def test_time_and_reported_token_budgets_hold_at_safe_boundary(store, monkeypatch):
    import backend.sessions as sessions
    session_id = create(store, budgets={'max_seconds': 5, 'max_iterations': 4, 'max_tokens': 100})
    clock = [1000]
    monkeypatch.setattr(sessions.time, 'time', lambda: clock[0])
    store.control(session_id, 'continue')
    lease = store.begin_step(session_id, 'Bounded CAD operation.')
    clock[0] = 1006
    stopped = store.finish_step(session_id, lease['operation_id'], 'Finished safely.', tokens_used=100)
    assert stopped['status'] == 'review'
    code('budget_reached', store.control, session_id, 'continue')
    store.control(session_id, 'settings', settings={'budgets': {'max_seconds': 10, 'max_iterations': 4, 'max_tokens': 200}})
    assert store.get(session_id)['status'] == 'review'
    store.control(session_id, 'continue')
    assert store.get(session_id)['status'] == 'running'


def test_real_session_simulation_preserves_inputs_and_human_pause(store, monkeypatch):
    import backend.sessions as sessions
    session_id = create(store, budgets={'max_iterations': 1})
    store.attach_baseline(session_id, STL.read_bytes())
    store.publish(session_id, 'prediction', 'The heavy case will exceed the saved movement limit.', phase='plan')
    store.control(session_id, 'continue')
    solver = sessions.run_test
    def interrupted_solver(*args, **kwargs):
        store.control(session_id, 'interrupt', 'Stop after this solve so we can discuss it.')
        return solver(*args, **kwargs)
    monkeypatch.setattr(sessions, 'run_test', interrupted_solver)
    result = store.run_simulation(session_id, TEST)
    session = result['session']
    assert session['status'] == 'paused' and session['usage']['iterations'] == 1
    assert session['pause_reason'] == 'Stop after this solve so we can discuss it.'
    assert session['report']['criteria_status'] == 'not_met'
    report = store.artifact(session_id, session['report']['artifact'])
    spec = store.artifact(session_id, session['report']['test_artifact'])
    assert spec.is_file() and report.is_file()
    assert result['report']['source']['sha256'] == session['revision']['sha256']
    assert 'fields' not in result['report']['load_cases'][0]
    code('budget_reached', store.control, session_id, 'continue')


def test_solver_failure_is_preserved_and_does_not_resume(store):
    session_id = create(store)
    store.control(session_id, 'continue')
    code('invalid_session', store.run_simulation, session_id, TEST)
    session = store.get(session_id)
    assert session['status'] == 'paused' and session['active_operation'] is None
    assert session['usage']['iterations'] == 1
    assert store.events(session_id)['events'][-1]['kind'] == 'step_failed'


def test_numerical_stopping_criteria_and_report_lineage_keep_acceptance_separate(store, tmp_path):
    import json
    spec = json.loads(TEST.read_text())
    for case in spec['load_cases']:
        case['max_displacement_mm'] = 10
    test_path = tmp_path / 'met-test.json'
    test_path.write_text(json.dumps(spec))
    session_id = create(store)
    store.control(session_id, 'continue')
    lease = store.begin_step(session_id, 'Preserve editable R0.')
    store.finish_step(session_id, lease['operation_id'], 'Baseline saved.', stl_path=STL,
                      revision='R0', cad_reference='cad:immutable-R0')
    first = store.run_simulation(session_id, test_path)
    assert first['session']['status'] == 'review'
    assert first['report']['criteria_status'] == 'met'
    assert first['session']['revision']['cad_reference'] == 'cad:immutable-R0'
    assert store.events(session_id)['events'][-1]['data']['acceptance'] == 'human_review_required'
    # Disabling automatic numerical stopping does not resume the session.
    store.control(session_id, 'settings', settings={'stop_on_criteria': False})
    assert store.get(session_id)['status'] == 'review'
    store.control(session_id, 'continue')
    second = store.run_simulation(session_id, test_path)
    assert second['session']['status'] == 'running'
    assert second['report']['iteration']['parent']['report_path'].endswith(first['session']['report']['artifact'])
    assert second['report']['iteration']['parent_test_matches']


def test_local_agent_session_connects_only_to_one_browser(store):
    session_id = create(store)
    store.connect(session_id, 'browser-a')
    assert store.get(session_id, 'browser-a')['id'] == session_id
    code('session_not_found', store.connect, session_id, 'browser-b')


def test_session_validation_and_lesson_provenance(store):
    code('invalid_session', create, store, budgets={'max_seconds': float('nan')})
    session_id = create(store)
    code('invalid_session', store.publish, session_id, 'lesson', 'A simulated gain is proof of safe load capacity.')
    code('invalid_session', store.publish, session_id, 'decision', 'Bad data.', {'value': float('inf')})
    code('invalid_session', store.control, session_id, 'settings', settings={'part_id': 'some-other-part'})
    code('invalid_session', store.events, session_id, after=-1)


def test_competing_clients_cannot_acquire_the_same_step(store):
    from concurrent.futures import ThreadPoolExecutor
    session_id = create(store)
    store.control(session_id, 'continue')
    def acquire(_):
        try:
            return SessionStore(store.root).begin_step(session_id, 'Concurrent CAD edit.')
        except AgentError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(acquire, range(2)))
    assert len([result for result in results if isinstance(result, dict)]) == 1
    assert 'operation_active' in results


def test_cli_commands_from_outside_checkout_use_the_shared_store(tmp_path):
    import json
    import os
    import subprocess
    environment = {**os.environ, 'MAKERSIM_SESSION_DIR': str(tmp_path / 'sessions')}
    def command(*args):
        result = subprocess.run([str(ROOT / '.venv/bin/makersim'), 'session', *args],
                                cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=10)
        return result.returncode, json.loads(result.stdout)
    status, created = command('create', '--objective', 'Explain a rib.', '--stl', str(STL))
    assert status == 0 and created['revision']['label'] == 'R0'
    status, blocked = command('begin', created['id'], '--description', 'Draw a rib.')
    assert status == 2 and blocked['error']['code'] == 'session_paused'
    assert command('control', created['id'], 'continue')[0] == 0
    status, thinking = command('activity', created['id'], 'thinking', '--message', 'Considering the load path.')
    assert status == 0 and thinking['state']['activity']['kind'] == 'thinking'
    status, lease = command('begin', created['id'], '--description', 'Draw a rib.')
    assert status == 0
    command('control', created['id'], 'interrupt', '--message', 'Explain the rib placement.')
    status, finished = command('finish', created['id'], lease['operation_id'], '--explanation', 'Edit preserved.')
    assert status == 0 and finished['status'] == 'paused'
