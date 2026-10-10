// Describe reported work honestly. A quiet connection cannot tell us what a model is doing.
const overdueSeconds = 60;
export function timeAgo(seconds) {
  if (seconds < 2) return 'just now';
  if (seconds < 60) return `${Math.floor(seconds)} seconds ago`;
  if (seconds < 3600) { const minutes = Math.floor(seconds / 60); return `${minutes} minute${minutes === 1 ? '' : 's'} ago`; }
  const hours = Math.floor(seconds / 3600); return `${hours} hour${hours === 1 ? '' : 's'} ago`;
}

export function agentPresence(session, {now = Date.now() / 1000, connection = 'live'} = {}) {
  const run = session?.agent_run;
  const fresh = !!run && run.state !== 'ended' && run.viewer_epoch === session.viewer_epoch && now - run.last_seen_at < overdueSeconds;
  return {fresh, connected: fresh && connection === 'live', run,
    controls: fresh && connection === 'live' && run.state === 'working' && run.supports_controls};
}

export function describeActivity(session, {now = Date.now() / 1000, connection = 'connecting'} = {}) {
  if (!session) return {label: 'Ready to start', tone: 'waiting',
    detail: 'Choose a part and describe what you want to build or learn.', freshness: '', connection: ''};
  const op = session.active_operation;
  const activity = session.activity;
  const updated = op ? (op.updated_at || op.started_at) : activity?.updated_at;
  const age = updated ? Math.max(0, now - updated) : null;
  const stale = age !== null && age >= overdueSeconds;
  const result = {label: '', tone: 'waiting', detail: '', freshness: '', connection: ''};
  const presence = agentPresence(session, {now, connection});
  const service = connection === 'live' ? 'MakerSim updates connected' : connection === 'connecting' ?
    'Checking the MakerSim connection…' : 'MakerSim updates disconnected · reconnecting. Live agent activity is unconfirmed.';
  const agent = presence.connected ? `${presence.run.name} connected${presence.run.supports_controls ? ' · optional design controls' : ' · viewing and feedback'}` :
    presence.run && presence.run.state !== 'ended' ? `${presence.run.name} has no confirmed live connection` : 'No agent connected';
  result.connection = `${service} · ${agent}`;
  if (!presence.connected) {
    result.label = session.status === 'review' ? 'Saved work · ready for review' : 'Saved work';
    result.tone = session.status === 'review' ? 'review' : 'waiting';
    result.detail = 'Your part, history and questions are saved. Connect your chosen agent app when you want to work.';
    if (session.active_operation) result.detail += ' An earlier step has no confirmed live agent. Review the saved history before starting a new run.';
    if (presence.run && presence.run.state !== 'ended') result.detail += ' The previous agent may still be working elsewhere; its current activity is unconfirmed.';
    if (session.status === 'review') result.detail += ' Review the results; fit, printing and physical checks remain separate.';
    result.freshness = session.updated_at ? `Last saved update ${timeAgo(Math.max(0, now - Date.parse(session.updated_at) / 1000))}.` : '';
    return result;
  }
  if (presence.run.state === 'connected') {
    result.label = `${presence.run.name} connected`;
    result.detail = 'Choose what to do next in your agent app. Connecting has not started design work.';
    return result;
  }
  const sketch = op?.kind !== 'simulation';
  if (session.status === 'review') {
    result.label = 'Ready for your review'; result.tone = 'review';
    result.detail = `${session.pause_reason || 'Review the latest design and results.'} Simulation results still need fit, print and physical checks.`;
  } else if (session.status === 'paused') {
    result.label = op ? `Pausing · ${sketch ? 'sketch' : 'simulation'} finishing` : 'Paused · waiting for you';
    result.tone = 'paused';
    result.detail = session.pause_reason || 'Your work is saved. Select Continue when you are ready.';
    if (op) result.detail += ` ${op.progress || op.description} This step can finish saving; the next step waits for your explicit resume.`;
    else if (activity && !stale && activity.kind !== 'waiting') result.detail += ` ${activity.kind === 'explaining' ? 'Explaining' : 'Thinking'}: ${activity.message} Design work remains paused.`;
    else result.detail += presence.controls ? ` Resume in ${presence.run.name} or select Continue here.` : ` Resume in ${presence.run.name} when ready.`;
  } else if (op) {
    result.label = sketch ? 'Sketching your part' : 'Simulating a load'; result.tone = stale ? 'waiting' : 'working';
    result.detail = op.progress || op.description;
    const elapsed = Math.max(0, Math.floor(now - op.started_at));
    result.freshness = `Step running for ${elapsed < 60 ? `${elapsed} seconds` : `${Math.floor(elapsed / 60)} minutes`}.`;
  } else if (activity && !stale && activity.kind !== 'waiting') {
    result.label = activity.kind === 'explaining' ? 'Explaining the design' : 'Thinking through the design';
    result.tone = 'working'; result.detail = activity.message;
  } else {
    result.label = stale ? 'Waiting for an update' : 'Waiting for the agent';
    result.detail = stale ? `Last reported: ${activity.message}` : activity?.message ||
      'This window is ready. Your connected agent has not started a step. Copy the session ID below if it still needs to connect.';
  }
  if (stale && (op || activity.kind !== 'waiting')) {
    result.tone = 'waiting';
    result.freshness += ` No progress update for ${timeAgo(age).replace(' ago', '')}. Work may still be running; its progress is unconfirmed.`;
  } else if (age !== null) result.freshness += ` Agent update ${timeAgo(age)}.`;
  else if (session.updated_at) result.freshness = `Last session update ${timeAgo(Math.max(0, now - Date.parse(session.updated_at) / 1000))}.`;
  return result;
}

const phases = {plan: 'Plan', do: 'Build', check: 'Test', act: 'Learn'};
const events = {created: 'Started a design session', continued: 'You selected Continue', settings: 'Settings changed',
  intervention: 'Your question or direction', recovered: 'Reset a stuck step', plan: 'Plan', prediction: 'Expected result',
  explanation: 'Explanation', decision: 'Design choice', lesson: 'What we learned', checkpoint: 'Step to review',
  step_started: 'Step started', step_finished: 'Step finished', step_failed: 'Step needs attention', geometry: 'Saved part version',
  result: 'Simulation results', branched: 'Created a separate copy', review_ready: 'Ready for review', activity: 'Agent activity',
  agent_connected: 'Agent connected', agent_disconnected: 'Agent disconnected', run_started: 'You started a run', feedback: 'Your question or direction'};
export function eventLabel(event) {
  return `#${event.sequence} · ${event.phase ? phases[event.phase] + ' · ' : ''}${events[event.kind] || event.kind.replaceAll('_', ' ')}`;
}
