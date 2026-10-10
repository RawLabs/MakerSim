import './agent-window.css';
import { viewportResult } from './session-display.js';
import { FloatingWindow } from './floating-window.js';
import { describeActivity, eventLabel, agentPresence } from './agent-activity.js';

const storageKey = 'makersim.activeDesignSession';
const sessionLabel = session => `${session.project} / ${session.part} · ${session.id.slice(0, 6)} · ${session.status === 'review' ? 'Ready for review' : 'Saved work'}`;
async function request(path = '', body) {
  let response;
  try {
    response = await fetch('/api/sessions' + path, {signal: AbortSignal.timeout(15000), ...(body === undefined ? {} : {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    })});
  } catch { throw new Error('Cannot reach MakerSim. Check that the local server is running, then try again.'); }
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    const failure = new Error(typeof error.detail === 'string' ? error.detail : `The session could not be updated (error ${response.status}). Try again.`);
    failure.status = response.status; throw failure;
  }
  return response.json();
}

function savedSession() {
  try { return localStorage.getItem(storageKey); } catch { return null; }
}

export class AgentWindow {
  constructor({part, showGeometry, showResult, clearViewport, notice}) {
    this.part = part; this.showGeometry = showGeometry; this.showResult = showResult; this.clearViewport = clearViewport; this.notice = notice;
    this.events = []; this.session = null; this.source = null; this.replaying = false; this.loadGeneration = 0; this.previewGeneration = 0;
    this.renderQueue = Promise.resolve(); this.previewKey = null; this.lastNotification = 0; this.connection = 'connecting';
    this.previewAbort = new AbortController();
    this.element = document.createElement('section');
    this.element.className = 'agent-window'; this.element.hidden = true;
    this.element.setAttribute('aria-label', 'Engineering agent');
    this.element.innerHTML = `
      <header class="agent-heading"><button type="button" class="agent-title" data-ui="title"><i class="agent-indicator" data-ui="indicator" aria-hidden="true"></i><strong>Engineering agent</strong><span>Build · Explain · Learn</span></button>
        <button type="button" data-ui="maximize" aria-label="Expand agent to full screen">⤢</button>
        <button type="button" data-ui="collapse" aria-label="Collapse agent to title pill">−</button>
        <button type="button" data-ui="close" aria-label="Close Engineering agent" title="Close Engineering agent">×</button></header>
      <div class="agent-activity" data-ui="activity"><strong data-ui="status" role="status"></strong><p data-ui="reason"></p><small data-ui="freshness"></small><small data-ui="connection"></small></div>
      <p data-ui="error" class="agent-error" role="status" hidden></p>
      <div class="agent-content">
        <div class="agent-session-picker"><label>Design session<select data-ui="sessions"><option value="">Start a new session</option></select></label></div>
        <form data-ui="create" class="agent-create">
          <div class="agent-two"><label>Project<input name="project" value="My project" maxlength="200" required></label>
            <label>Part<input name="part" value="New part" maxlength="200" required></label></div>
          <label>What shall we build or explain?<textarea name="objective" placeholder="Use a copy of this bracket to show and explain a rib." maxlength="5000" required></textarea></label>
          <div class="agent-two"><label>Starting point<select name="source"><option value="copy">Copy selected part</option><option value="selected">Selected part</option><option value="scratch">From scratch</option></select></label>
            <label>How we work<select name="mode"><option value="interactive">Work together</option><option value="auto">Work on its own</option><option value="teaching">Show and explain</option></select></label></div>
          <button type="submit" class="agent-primary">Save this design session</button>
          <p>Keep this part’s work and learning together. Connect your chosen AI agent to see it build and explain. Saving or reopening does not start an agent.</p>
        </form>
        <form data-ui="connect" class="agent-connect"><label>Connect work your agent already started<input name="session_id" placeholder="Paste its session ID" minlength="32" maxlength="32" required></label><button type="submit">Connect</button></form>
        <div data-ui="active" hidden>
          <p data-ui="scope" class="agent-scope"></p><p data-ui="objective" class="agent-objective"></p>
          <p data-ui="usage" class="agent-usage"></p>
          <div class="agent-connection"><strong>Use your chosen agent app</strong><p>Give your agent this session ID and tell it what you want to do. Its MakerSim connection reports its name and activity here.</p><code data-ui="id"></code><button type="button" data-ui="copy">Copy ID</button></div>
          <div class="agent-two"><label>How we work<select data-ui="mode"><option value="interactive">Work together</option><option value="auto">Work on its own</option><option value="teaching">Show and explain</option></select></label>
            <label class="agent-check" data-ui="checkpoint-option" hidden><input type="checkbox" data-ui="checkpoints">Wait for me after each step</label></div>
          <label class="agent-check"><input type="checkbox" data-ui="notify">Notify when ready for review</label>
          <div data-ui="managed" hidden>
          <small data-ui="control-note"></small>
          <label class="agent-check"><input type="checkbox" data-ui="criteria">Stop when the saved test goals are met</label>
          <details class="agent-budget"><summary>Work limits</summary><form data-ui="budgets">
            <label>Maximum simulation runs<input name="max_iterations" type="number" min="1" max="1000" required></label>
            <label>Build and test time (seconds)<input name="max_seconds" type="number" min="1" max="86400" required></label>
            <label>AI usage limit (tokens, optional)<input name="max_tokens" type="number" min="1" max="10000000"></label>
            <small>Limits are checked between steps. Text planning time is separate. Tokens measure AI usage reported by your agent; this is not a billing cap.</small><button type="submit">Save limits</button>
          </form></details>
          <div class="agent-controls"><button type="button" data-ui="pause">Pause work</button><button type="button" data-ui="continue" class="agent-primary">Continue</button></div>
          <div data-ui="recover" hidden><small>If the connected agent has stopped, reset its unfinished step before continuing. This does not stop an external CAD tool.</small><button type="button" data-ui="reset">Reset a stuck step</button></div>
          </div>
          <form data-ui="intervene"><label>Ask a question or change direction<textarea name="message" placeholder="Why make this rib so thick? Leave room for the bolts." maxlength="5000" required></textarea></label><button type="submit" data-ui="send">Save question</button><small data-ui="feedback-note">Saved with this part so your agent can read it when it connects.</small></form>
          <div class="agent-history-controls"><label>View<select data-ui="filter"><option value="all">Design history</option><option value="memory">What we learned</option></select></label><button type="button" data-ui="live">Back to current work</button></div>
          <label class="agent-replay">Explore earlier steps<input data-ui="replay" type="range" min="1" max="1" value="1" aria-label="Explore design history"></label>
          <p data-ui="viewing" hidden></p>
          <ol data-ui="timeline" class="agent-timeline" aria-label="Design timeline" aria-live="polite"></ol>
        </div>
      </div>`;
    document.body.append(this.element);
    this.button = document.createElement('button'); this.button.id = 'open-agent';
    this.button.className = 'quiet-button'; this.button.textContent = 'Agent';
    this.button.setAttribute('aria-expanded', 'false');
    document.querySelector('.top-actions').prepend(this.button);
    this.button.onclick = () => this.open();
    this.window = new FloatingWindow(this.element, {title: this.ui('title'), collapse: this.ui('collapse'),
      close: this.ui('close'), maximize: this.ui('maximize'),
      onMode: mode => this.button.setAttribute('aria-expanded', String(mode !== 'pill')),
      onClose: () => {
        this.button.setAttribute('aria-expanded', 'false');
        this.button.focus({preventScroll: true});
      }});
    this.ui('create').onsubmit = event => this.perform(async () => {
      event.preventDefault();
      const form = new FormData(event.currentTarget), selected = this.part();
      const spec = Object.fromEntries(form);
      if (spec.source !== 'scratch' && !selected.file) throw new Error('Select a part first, or choose From scratch.');
      const session = await request('', spec);
      // Attach before exposing Continue; incomplete baseline uploads remain paused.
      if (spec.source !== 'scratch') {
        const body = new FormData(); body.append('file', selected.file); body.append('unit', selected.unit);
        const response = await fetch(`/api/sessions/${session.id}/geometry`, {method: 'POST', body});
        if (!response.ok) { await this.select(session.id); throw new Error('The session was saved, but its starting part could not be preserved. Create a new session before continuing.'); }
      }
      await this.refreshSessions(session.id); await this.select(session.id);
    });
    this.ui('connect').onsubmit = event => this.perform(async () => {
      event.preventDefault(); const id = new FormData(event.currentTarget).get('session_id').trim();
      await request('/connect', {session_id: id}); await this.refreshSessions(id); await this.select(id);
    });
    this.ui('sessions').onchange = () => this.perform(() => this.select(this.ui('sessions').value));
    this.ui('copy').onclick = () => this.perform(async () => {
      await navigator.clipboard.writeText(this.session.id); this.ui('copy').textContent = 'Copied';
    });
    this.ui('pause').onclick = () => this.control('pause');
    this.ui('continue').onclick = () => this.control('continue');
    this.ui('reset').onclick = () => this.control('recover');
    this.ui('mode').onchange = () => this.control('settings', '', {mode: this.ui('mode').value});
    this.ui('checkpoints').onchange = () => this.control('settings', '', {pause_checkpoints: this.ui('checkpoints').checked});
    this.ui('notify').onchange = () => this.control('settings', '', {notify_when_done: this.ui('notify').checked});
    this.ui('criteria').onchange = () => this.control('settings', '', {stop_on_criteria: this.ui('criteria').checked});
    this.ui('budgets').onsubmit = event => {
      event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget));
      this.control('settings', '', {budgets: {max_iterations: Number(values.max_iterations),
        max_seconds: Number(values.max_seconds), max_tokens: values.max_tokens ? Number(values.max_tokens) : null}});
    };
    this.ui('intervene').onsubmit = async event => {
      event.preventDefault(); const input = event.currentTarget.elements.message;
      const message = input.value;
      const managed = agentPresence(this.session, {connection: this.connection}).controls;
      const saved = await this.control(managed ? 'interrupt' : 'feedback', message);
      if (saved && input.value === message) input.value = '';
    };
    this.ui('filter').onchange = () => this.renderTimeline();
    this.ui('replay').oninput = () => {
      this.replaying = true; const event = this.events.find(event => event.sequence === Number(this.ui('replay').value));
      if (event) this.queuePreview(event.state, event.sequence);
    };
    this.ui('live').onclick = () => { this.replaying = false; this.ui('replay').value = this.session.sequence; this.queuePreview(this.session); };
    this.activityTimer = setInterval(() => this.renderActivity(), 1000);
    window.addEventListener('pagehide', () => { this.source?.close(); this.window.dispose(); clearInterval(this.activityTimer); });
    this.renderActivity();
    this.restore();
  }

  ui(name) { return this.element.querySelector(`[data-ui="${name}"]`); }
  async perform(action) {
    this.ui('error').hidden = true;
    try { return await action(); } catch (error) {
      this.loading = false; this.renderActivity(); this.ui('error').textContent = error.message; this.ui('error').hidden = false;
    }
  }
  async restore() {
    let sessions;
    try {
      sessions = await request();
    } catch (error) {
      if (error.status === 404) this.button.hidden = true; // Sessions are disabled in the hosted preview.
      else { this.ui('error').textContent = error.message; this.ui('error').hidden = false; }
      return;
    }
    this.populateSessions(sessions.sessions);
    const id = savedSession();
    if (sessions.sessions.some(session => session.id === id)) await this.perform(() => this.select(id));
  }
  async open() {
    this.window.open();
    if (!this.session) this.ui('create').elements.part.value = this.part().file?.name || 'New part';
    await this.perform(() => this.refreshSessions(this.session?.id));
  }
  populateSessions(sessions, selected = this.session?.id || '') {
    const select = this.ui('sessions'); select.replaceChildren(new Option('Start a new session', ''));
    for (const session of sessions) select.add(new Option(sessionLabel(session), session.id));
    select.value = selected;
  }
  async refreshSessions(selected) { this.populateSessions((await request()).sessions, selected); }
  async select(id) {
    this.source?.close(); this.source = null; this.loadGeneration++; this.previewGeneration++; this.previewKey = null;
    this.previewAbort.abort(); this.previewAbort = new AbortController();
    this.ui('viewing').hidden = true; this.ui('viewing').textContent = '';
    if (id) this.clearViewport();
    this.events = []; this.session = null; this.replaying = false; this.loading = !!id; this.connection = 'connecting';
    this.button.textContent = 'Agent'; this.renderActivity();
    this.ui('id').textContent = '';
    this.ui('copy').textContent = 'Copy ID';
    this.ui('continue').disabled = true;
    try { if (id) localStorage.setItem(storageKey, id); else localStorage.removeItem(storageKey); } catch { /* Session picker still works. */ }
    this.ui('active').hidden = !id; this.ui('create').hidden = !!id; this.ui('connect').hidden = !!id;
    this.ui('sessions').value = id;
    if (!id) return;
    const generation = this.loadGeneration;
    let cursor = 0;
    do {
      const page = await request(`/${id}/events?after=${cursor}&limit=100`);
      if (generation !== this.loadGeneration) return;
      this.events.push(...page.events); this.session = page.session; cursor = page.next_after;
      if (!page.events.length) break;
    } while (cursor < this.session.sequence);
    this.lastNotification = this.session.sequence;
    this.loading = false;
    this.render(); this.queuePreview(this.session);
    this.source = new EventSource(`/api/sessions/${id}/stream?after=${cursor}`);
    this.source.onopen = async () => {
      if (generation !== this.loadGeneration) return;
      this.connection = 'live'; this.renderActivity();
      // A restarted service has a new viewing epoch; browser SSE alone is not agent presence.
      try {
        const latest = await request(`/${id}`);
        if (generation === this.loadGeneration && latest.sequence >= this.session.sequence) { this.session = latest; this.renderState(); }
      } catch { if (generation === this.loadGeneration) { this.connection = 'disconnected'; this.renderActivity(); } }
    };
    this.source.onerror = () => { if (generation === this.loadGeneration) { this.connection = 'disconnected'; this.renderActivity(); } };
    this.source.addEventListener('session', message => {
      if (generation !== this.loadGeneration) return;
      const event = JSON.parse(message.data);
      if (event.sequence <= (this.events.at(-1)?.sequence || 0)) return;
      this.events.push(event);
      if (event.sequence >= this.session.sequence) this.session = {...this.session, ...event.state};
      this.render();
      if (!this.replaying && ['geometry', 'result', 'branched'].includes(event.kind)) this.queuePreview(this.session);
      if (event.kind === 'review_ready' && this.session.notify_when_done && event.sequence > this.lastNotification) {
        this.lastNotification = event.sequence; this.notice(event.message);
        this.button.textContent = 'Agent · Review ready';
        if (document.hidden) document.title = 'Review ready · MakerSim';
      }
    });
  }
  control(action, message = '', settings) {
    return this.perform(async () => {
      if(!this.session) throw new Error('Wait for the session to finish loading.');
      const id = this.session.id;
      const session = await request(`/${id}/control`, {action, message, ...(settings ? {settings} : {})});
      if (this.session?.id === id) { this.session = session; this.renderState(); }
      return session;
    });
  }
  async beforePartChange() {
    if (this.session?.status === 'running' && agentPresence(this.session, {connection: this.connection}).controls) {
      const session = await request(`/${this.session.id}/control`, {action: 'interrupt', message: 'Human selected a different viewport part. Review the session part before Continue.'});
      this.session = session; this.renderState();
    }
    // The session retains its selected part. Do not overwrite a user's replacement with live exports.
    if (this.session) this.replaying = true;
    this.previewGeneration++;
    this.previewKey = null;
    this.previewAbort.abort(); this.previewAbort = new AbortController();
  }
  render() { this.renderState(); this.renderTimeline(); }
  renderState() {
    const s = this.session; if (!s) return;
    const option = [...this.ui('sessions').options].find(option => option.value === s.id);
    if (option) option.textContent = sessionLabel(s);
    this.ui('scope').textContent = `${s.project} / ${s.part}${s.parent ? ' · separate copy' : ''}`;
    this.ui('objective').textContent = s.objective;
    this.renderActivity();
    this.ui('usage').textContent = `${s.usage.iterations} of ${s.budgets.max_iterations} simulations used · ${Math.round(s.usage.seconds)} seconds of build/test time`;
    this.ui('id').textContent = s.id; this.ui('mode').value = s.mode;
    this.ui('checkpoints').checked = s.pause_checkpoints; this.ui('notify').checked = s.notify_when_done;
    this.ui('criteria').checked = s.stop_on_criteria;
    // Leave fields alone while the user is editing a budget.
    if (!this.ui('budgets').contains(document.activeElement)) {
      for (const key of ['max_iterations', 'max_seconds', 'max_tokens']) this.ui('budgets').elements[key].value = s.budgets[key] ?? '';
    }
    this.ui('replay').max = s.sequence;
    if (!this.replaying) this.ui('replay').value = s.sequence;
  }
  renderActivity() {
    const state = describeActivity(this.session, {connection: this.connection});
    if (this.loading) { state.label = 'Loading your saved work…'; state.detail = 'Retrieving this part’s history and latest version.'; }
    const set = (name, value) => { const element = this.ui(name); if (element.textContent !== value) element.textContent = value; };
    set('status', state.label); set('reason', state.detail); set('freshness', state.freshness.trim()); set('connection', state.connection);
    this.ui('activity').dataset.tone = state.tone; this.ui('indicator').dataset.tone = state.tone;
    this.ui('title').title = `Engineering agent · ${state.label}${state.connection ? ' · ' + state.connection : ''}`;
    this.ui('title').setAttribute('aria-label', this.ui('title').title);
    const presence = agentPresence(this.session, {connection: this.connection});
    this.ui('managed').hidden = !presence.controls; this.ui('checkpoint-option').hidden = !presence.controls;
    this.ui('continue').disabled = !presence.controls || this.session?.status === 'running' || !!this.session?.active_operation;
    this.ui('pause').disabled = !presence.controls || this.session?.status !== 'running';
    this.ui('recover').hidden = !presence.controls || !this.session?.active_operation || this.session?.status !== 'paused';
    this.ui('control-note').textContent = presence.controls ? `Optional controls for ${presence.run.name}’s design steps. They do not start its app.` : '';
    set('send', presence.controls ? 'Send and pause' : presence.connected ? 'Send question' : 'Save question');
    set('feedback-note', presence.controls ? 'Design steps wait for an explicit resume here or in your agent app.' :
      presence.connected ? `Saved in this part’s history for ${presence.run.name} to read. Control work in your agent app.` :
      'Saved with this part so your agent can read it when it connects.');
  }
  renderTimeline() {
    const list = this.ui('timeline'), nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
    list.replaceChildren();
    for (const event of this.events) {
      if (event.kind === 'agent_heartbeat' || event.kind === 'activity' && event.data.heartbeat) continue;
      if (this.ui('filter').value === 'memory' && !['lesson', 'intervention', 'feedback', 'decision'].includes(event.kind)) continue;
      const entry = document.createElement('li'); entry.dataset.sequence = event.sequence;
      const label = document.createElement('small'); label.textContent = eventLabel(event);
      const message = document.createElement('p'); message.textContent = event.message; entry.append(label, message);
      if (event.data.evidence_type) {
        const evidence = document.createElement('small'); evidence.textContent = `Based on: ${{hypothesis: 'an idea to test', simulation: 'a simulation', human_observation: 'your observation', physical_test: 'a physical test'}[event.data.evidence_type] || event.data.evidence_type}`; entry.append(evidence);
      }
      if (Object.keys(event.data).length) {
        const details = document.createElement('details'), summary = document.createElement('summary'), data = document.createElement('pre');
        summary.textContent = 'Details and evidence'; data.textContent = JSON.stringify(event.data, null, 2); details.append(summary, data); entry.append(details);
      }
      if (['geometry', 'result'].includes(event.kind)) {
        const view = document.createElement('button'); view.type = 'button'; view.textContent = 'View this version';
        view.onclick = () => { this.replaying = true; this.ui('replay').value = event.sequence; this.queuePreview(event.state, event.sequence); }; entry.append(view);
      }
      const branch = document.createElement('button'); branch.type = 'button'; branch.textContent = 'Try a copy from here';
      branch.onclick = () => this.perform(async () => {
        const session = await request(`/${this.session.id}/fork`, {sequence: event.sequence});
        await this.refreshSessions(session.id); await this.select(session.id);
      });
      entry.append(branch); list.append(entry);
    }
    if (nearBottom) list.scrollTop = list.scrollHeight;
  }
  queuePreview(snapshot, sequence = null) {
    this.ui('viewing').hidden = sequence === null;
    this.ui('viewing').textContent = sequence === null ? '' : `Viewing step #${sequence}. The activity above describes current work. Select Back to current work to see the latest part.`;
    const generation = this.loadGeneration;
    const previewGeneration = this.previewGeneration;
    const signal = this.previewAbort.signal;
    const obsolete = () => generation !== this.loadGeneration || previewGeneration !== this.previewGeneration;
    this.renderQueue = this.renderQueue.catch(() => {}).then(async () => {
      if (obsolete()) return;
      if (!snapshot.revision) { this.clearViewport(); this.previewKey = null; return; }
      const sessionId = this.session.id, key = `${sessionId}/${snapshot.revision.artifact}`;
      const artifact = name => `/api/sessions/${sessionId}/artifacts/${encodeURIComponent(name)}`;
      if (key !== this.previewKey) {
        const response = await fetch(artifact(snapshot.revision.artifact), {signal});
        if (!response.ok) throw new Error('The saved geometry could not be loaded.');
        const file = new File([await response.blob()], `${snapshot.revision.label}.stl`);
        if (obsolete()) return;
        const shown = await this.showGeometry(file, snapshot.revision.unit, {obsolete, signal});
        if (obsolete() || shown === false) return;
        this.previewKey = key;
      }
      if (snapshot.report) {
        const response = await fetch(artifact(snapshot.report.artifact), {signal});
        if (!response.ok) throw new Error('The saved simulation could not be loaded.');
        const report = await response.json();
        if (obsolete()) return;
        this.showResult(viewportResult(report, snapshot.revision.source_center_mm));
      } else this.showResult(null);
    }).catch(error => { if (!obsolete()) { this.ui('error').textContent = error.message; this.ui('error').hidden = false; } });
  }
}
