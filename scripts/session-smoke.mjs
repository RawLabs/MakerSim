// Real localhost + SSE browser check. Session state is isolated in a temporary directory.
import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, mkdir, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const moduleName = process.env.MAKERSIM_PLAYWRIGHT_MODULE || 'playwright';
const { chromium } = await import(moduleName.startsWith('/') ? pathToFileURL(moduleName).href : moduleName);
const directory = await mkdtemp(resolve(tmpdir(), 'makersim-session-qa-'));
const environment = {...process.env, MAKERSIM_SESSION_DIR: directory, MAKERSIM_HOSTED: '0'};
const execute = promisify(execFile);
const runIds = new Map();
const command = async (...args) => {
  if (['event', 'activity', 'begin', 'test', 'complete'].includes(args[0]) && runIds.has(args[1])) args.push('--run-id', runIds.get(args[1]));
  try {
    const result = await execute(resolve(root, '.venv/bin/makersim'), ['session', ...args], {cwd: root, env: environment});
    const value = JSON.parse(result.stdout);
    if (args[0] === 'agent') runIds.set(args[1], value.run_id);
    return {status: 0, value};
  } catch (error) {
    if (error.stdout) return {status: error.code, value: JSON.parse(error.stdout)};
    throw error;
  }
};
let server;
const startServer = async (port = '0') => {
  server = spawn(resolve(root, '.venv/bin/python'), ['-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', port], {cwd: root, env: environment});
  let serverLog = '';
  return new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error('Local session server did not start. ' + serverLog)), 10000);
  server.stderr.on('data', chunk => {
    serverLog += chunk;
    const match = serverLog.match(/Uvicorn running on (http:\/\/127\.0\.0\.1:\d+)/);
    if (match) { clearTimeout(timer); resolve(match[1]); }
  });
  server.on('exit', code => { clearTimeout(timer); reject(new Error(`Session server exited ${code}: ${serverLog}`)); });
  });
};
const url = await startServer();
const browser = await chromium.launch({executablePath: process.env.MAKERSIM_CHROMIUM || '/usr/bin/chromium', headless: true,
  args: ['--no-sandbox', '--enable-unsafe-swiftshader', '--use-angle=swiftshader']});
const context = await browser.newContext({viewport: {width: 1440, height: 1060}});
const page = await context.newPage();
const errors = []; page.on('pageerror', error => errors.push(error.message));
const panel = page.locator('.agent-window');
const shots = process.env.MAKERSIM_QA_ARTIFACTS || directory;
const closeSize = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1, `Expected ${expected}px, got ${actual}px.`);
try {
  await page.goto(url);
  assert.equal(await panel.isVisible(), false, 'The agent window starts hidden until Agent is selected.');
  assert.equal(await page.locator('#open-agent').getAttribute('aria-expanded'), 'false');
  await page.locator('#load-example').click();
  await page.waitForFunction(() => !document.querySelector('#simulate').disabled);
  assert.equal(await panel.isVisible(), false, 'Loading a part must not open the agent window.');
  await page.locator('#open-agent').click();
  const heading = panel.locator('.agent-heading');
  const dragTitle = async (dx, dy) => {
    const box = await panel.locator('[data-ui="title"]').boundingBox();
    const x = box.x + box.width/2, y = box.y + box.height/2;
    await page.mouse.move(x, y); await page.mouse.down();
    await page.mouse.move(x + dx, y + dy, {steps: 8}); await page.mouse.up();
  };
  const resizeCard = async (direction, dx, dy) => {
    const box = await panel.locator(`[data-resize="${direction}"]`).boundingBox();
    const x = box.x + box.width / 2, y = box.y + box.height / 2;
    await page.mouse.move(x, y); await page.mouse.down();
    await page.mouse.move(x + dx, y + dy, {steps: 8}); await page.mouse.up();
  };
  const initialCard = await panel.boundingBox();
  await dragTitle(-400, -200);
  const movedCard = await panel.boundingBox();
  assert.ok(Math.abs(movedCard.x - (initialCard.x - 400)) < 2);
  assert.ok(Math.abs(movedCard.y - (initialCard.y - 200)) < 2);
  await resizeCard('se', 140, -120);
  const cornerResized = await panel.boundingBox();
  closeSize(cornerResized.x, movedCard.x); closeSize(cornerResized.y, movedCard.y);
  closeSize(cornerResized.width, movedCard.width + 140); closeSize(cornerResized.height, movedCard.height - 120);
  await resizeCard('w', -80, 0);
  const edgeResized = await panel.boundingBox();
  closeSize(edgeResized.x, cornerResized.x - 80);
  closeSize(edgeResized.width, cornerResized.width + 80);
  await resizeCard('n', 0, -30);
  const topResized = await panel.boundingBox();
  closeSize(topResized.y, edgeResized.y - 30);
  closeSize(topResized.height, edgeResized.height + 30);
  await resizeCard('se', -1000, -1000);
  const minimumCard = await panel.boundingBox();
  assert.equal(minimumCard.width, 300); assert.equal(minimumCard.height, 280);
  await resizeCard('se', 340, 280);
  await panel.locator('[data-ui="resize"]').focus();
  await page.keyboard.press('ArrowRight'); await page.keyboard.press('Shift+ArrowDown');
  const resizedCard = await panel.boundingBox();
  closeSize(resizedCard.width, 660); closeSize(resizedCard.height, 610);
  const chosenLayout = await page.evaluate(() => localStorage.getItem('makersim.agentWindowLayout'));
  await page.evaluate(() => {
    window.dispatchEvent(new Event('blur'));
    window.dispatchEvent(new Event('focus'));
  });
  assert.deepEqual(await panel.boundingBox(), resizedCard, 'Focus changes preserve the resized card.');
  const otherTab = await page.context().newPage();
  await otherTab.bringToFront();
  await page.bringToFront();
  await otherTab.close();
  assert.deepEqual(await panel.boundingBox(), resizedCard, 'Returning from another tab preserves the resized card.');
  // A temporary reduction in available space may fit the card, but must never
  // become a new manual size, including when closing or changing presentation.
  await page.setViewportSize({width: 560, height: 420});
  await page.waitForFunction(() => {
    const box = document.querySelector('.agent-window').getBoundingClientRect();
    return box.x >= 8 && box.y >= 8 && box.right <= innerWidth - 8 && box.bottom <= innerHeight - 8;
  });
  const fittedCard = await panel.boundingBox();
  assert.ok(fittedCard.width < resizedCard.width && fittedCard.height < resizedCard.height);
  assert.equal(await page.evaluate(() => localStorage.getItem('makersim.agentWindowLayout')), chosenLayout,
    'Automatic fitting must not overwrite the manually chosen layout.');
  await panel.locator('[data-ui="close"]').click();
  await page.locator('#open-agent').click();
  assert.equal(await page.evaluate(() => localStorage.getItem('makersim.agentWindowLayout')), chosenLayout,
    'Close/reopen while fitted must retain the preferred layout.');
  await panel.locator('[data-ui="maximize"]').click();
  await panel.locator('[data-ui="maximize"]').click();
  await page.setViewportSize({width: 1440, height: 1060});
  await page.waitForFunction(expected => {
    const box = document.querySelector('.agent-window').getBoundingClientRect();
    return ['x', 'y', 'width', 'height'].every(key => Math.abs(box[key] - expected[key]) < 1);
  }, resizedCard);
  assert.deepEqual(await panel.boundingBox(), resizedCard, 'Returning to the original viewport restores the chosen size and position.');
  await panel.locator('[data-ui="collapse"]').click();
  assert.equal(await panel.getAttribute('data-window-mode'), 'pill');
  assert.equal(await panel.locator('.agent-content').isVisible(), false);
  assert.equal((await heading.locator('[data-ui="title"]').innerText()).trim(), 'Engineering agent');
  assert.equal(await heading.locator('[data-ui="close"]').isVisible(), true, 'The pill can also be closed.');
  const pill = await panel.boundingBox();
  assert.ok(pill.height < 60 && pill.width < 260);
  await dragTitle(-180, 40);
  assert.equal(await panel.getAttribute('data-window-mode'), 'pill', 'Dragging the pill must not open it.');
  const movedPill = await panel.boundingBox();
  assert.ok(Math.abs(movedPill.x - (pill.x - 180)) < 2);
  await panel.locator('[data-ui="close"]').click();
  assert.equal(await panel.isVisible(), false, 'Close hides the pill completely.');
  assert.equal(await page.locator('#open-agent').getAttribute('aria-expanded'), 'false');
  assert.equal(await page.locator('#open-agent').evaluate(element => document.activeElement === element), true);
  await page.locator('#open-agent').click();
  assert.equal(await panel.getAttribute('data-window-mode'), 'card');
  const restoredCard = await panel.boundingBox();
  assert.ok(Math.abs(restoredCard.x - movedPill.x) < 2);
  assert.ok(Math.abs(restoredCard.y - movedPill.y) < 2);
  closeSize(restoredCard.width, resizedCard.width); closeSize(restoredCard.height, resizedCard.height);
  assert.equal(await panel.locator('[data-ui="resize"]').isVisible(), true);
  await panel.locator('[data-ui="maximize"]').click();
  const fullScreen = await panel.boundingBox();
  assert.deepEqual(fullScreen, {x: 0, y: 0, width: 1440, height: 1060});
  assert.equal(await panel.locator('[data-ui="resize"]').isVisible(), false);
  await panel.locator('[data-ui="maximize"]').click();
  assert.deepEqual(await panel.boundingBox(), restoredCard, 'Restore preserves the floating card position and size.');
  await panel.locator('[name="objective"]').fill('Keep this unfinished instruction when closing.');
  await panel.locator('[data-ui="close"]').click();
  assert.equal(await panel.isVisible(), false, 'Close hides the floating card.');
  assert.equal(await page.locator('#open-agent').getAttribute('aria-expanded'), 'false');
  await page.locator('#open-agent').click();
  assert.deepEqual(await panel.boundingBox(), restoredCard, 'Reopening preserves the floating layout.');
  assert.equal(await panel.locator('[name="objective"]').inputValue(), 'Keep this unfinished instruction when closing.');
  await panel.locator('[data-ui="maximize"]').click();
  await panel.locator('[data-ui="close"]').click();
  assert.equal(await panel.isVisible(), false, 'Close also hides full screen.');
  await page.locator('#open-agent').click();
  assert.equal(await panel.getAttribute('data-window-mode'), 'card');
  assert.deepEqual(await panel.boundingBox(), restoredCard, 'Reopening full screen restores the saved floating card.');
  await panel.locator('[data-ui="title"]').focus();
  await page.keyboard.press('ArrowRight');
  const keyboardMoved = await panel.boundingBox();
  assert.ok(Math.abs(keyboardMoved.x - restoredCard.x - 20) < 2);
  await panel.locator('[name="objective"]').fill('Show and explain a rib on a copy of this bracket.');
  await panel.locator('[data-ui="create"] button[type="submit"]').click();
  await panel.locator('[data-ui="active"]').waitFor({state: 'visible'});
  await page.waitForFunction(() => document.querySelector('[data-ui="id"]').textContent.length === 32);
  const id = (await panel.locator('[data-ui="id"]').textContent()).trim();
  assert.equal(await panel.locator('[data-ui="status"]').innerText(), 'Saved work');
  assert.equal(await panel.locator('[data-ui="continue"]').isVisible(), false);
  await command('agent', id, '--name', 'Workshop CAD agent', '--controls');
  await page.waitForFunction(() => document.querySelector('[data-ui="status"]').textContent === 'Workshop CAD agent connected');
  assert.equal(await panel.locator('[data-ui="continue"]').isVisible(), false, 'Connecting alone does not start a run.');
  await command('authorize', id, runIds.get(id), 'start', '--human-instruction', 'Start explaining a rib on this bracket.');
  await page.waitForFunction(() => document.querySelector('[data-ui="status"]').textContent === 'Waiting for the agent');
  await command('activity', id, 'thinking', '--message', 'Considering how the rib carries force while leaving room for bolts.');
  await page.waitForFunction(() => document.querySelector('[data-ui="status"]').textContent === 'Thinking through the design');
  assert.match(await panel.locator('[data-ui="sessions"] option:checked').innerText(), /Saved work/);
  assert.equal(await panel.locator('[data-ui="indicator"]').getAttribute('data-tone'), 'working');
  await mkdir(shots, {recursive: true});
  await page.screenshot({path: resolve(shots, 'agent-session-thinking.png')});
  await command('event', id, 'prediction', '--message', 'A rib may reduce junction bending. <img src=x onerror=alert(1)>', '--phase', 'plan');
  const lease = (await command('begin', id, '--description', 'Preserve one CAD edit and export its geometry.')).value;
  assert.ok(lease.operation_id, JSON.stringify(lease));
  await page.waitForFunction(() => document.querySelector('[data-ui="status"]').textContent === 'Sketching your part');
  await page.route('**/api/sessions/*/control', route => route.fulfill({status: 503,
    contentType: 'application/json', body: JSON.stringify({detail: 'Connection test: server unavailable.'})}));
  await panel.locator('[data-ui="intervene"] textarea').fill('Keep this question if sending fails.');
  await panel.locator('[data-ui="intervene"] button').click();
  await panel.locator('[data-ui="error"]').waitFor({state: 'visible'});
  assert.equal(await panel.locator('[data-ui="intervene"] textarea').inputValue(), 'Keep this question if sending fails.');
  await page.unroute('**/api/sessions/*/control');
  await panel.locator('[data-ui="intervene"] textarea').fill('Why this rib thickness? Preserve the bolt clearance.');
  await panel.locator('[data-ui="intervene"] button').click();
  await page.waitForFunction(() => document.querySelector('[data-ui="status"]').textContent.includes('Pausing'));
  const blocked = await command('begin', id, '--description', 'Another edit must wait.');
  assert.equal(blocked.value.error.code, 'session_paused');
  await command('event', id, 'explanation', '--message', 'Thickness is a hypothesis. We will compare it with the simulation.');
  await command('finish', id, lease.operation_id, '--explanation', 'Current geometry saved at the interruption boundary.',
    '--stl', resolve(root, 'backend/data/backpack-bracket.stl'), '--revision', 'R1');
  await page.waitForFunction(() => document.querySelector('#file-name').textContent === 'R1.stl');
  assert.equal(await panel.locator('.agent-timeline img').count(), 0, 'Explanations render as text.');
  assert.match(await panel.locator('[data-ui="status"]').innerText(), /Paused/);
  assert.equal(await panel.locator('[data-ui="continue"]').isVisible(), true);
  await command('activity', id, 'explaining', '--message', 'A rib connects the loaded arm to the mounting wall.');
  await page.waitForFunction(() => document.querySelector('[data-ui="reason"]').textContent.includes('Design work remains paused'));
  assert.match(await panel.locator('[data-ui="status"]').innerText(), /Paused/);
  await command('event', id, 'explanation', '--message', 'The rib can reduce bending; its benefit still needs a test.');
  await page.reload();
  await page.waitForFunction(() => document.querySelector('#file-name').textContent === 'R1.stl');
  assert.equal(await panel.isVisible(), false, 'Restoring saved session work must not open the window.');
  await page.locator('#open-agent').click();
  assert.deepEqual(await panel.boundingBox(), keyboardMoved, 'Floating placement persists after reload.');
  assert.match(await panel.locator('[data-ui="status"]').innerText(), /Paused/);
  await panel.locator('[data-ui="checkpoints"]').check();
  await page.waitForFunction(() => document.querySelector('[data-ui="checkpoints"]').checked);
  await panel.locator('[data-ui="continue"]').click();
  const tested = await command('test', id, '--test', resolve(root, 'backend/data/bracket-test.json'));
  assert.equal(tested.status, 0, JSON.stringify(tested.value));
  await page.locator('#result-panel').waitFor({state: 'visible'});
  await page.waitForFunction(() => document.querySelector('[data-ui="status"]').textContent.includes('Paused'));
  assert.equal(await page.locator('#result-force').innerText(), '5 lb');
  assert.equal(await page.locator('#force-unit').inputValue(), 'lbf');
  await page.locator('#force-unit').selectOption('kg');
  assert.equal(await page.locator('#result-force').innerText(), '2.268 kg');
  await page.reload();
  await page.waitForFunction(() => document.querySelector('#result-force').textContent === '2.268 kg');
  await page.locator('#open-agent').click();
  assert.equal(await page.locator('#force-unit').inputValue(), 'kg', 'The chosen unit survives restored agent results.');
  await panel.locator('[data-ui="timeline"] li[data-sequence="2"] button').filter({hasText: 'View this version'}).click();
  await page.waitForFunction(() => document.querySelector('#file-name').textContent === 'R0.stl');
  assert.equal(await page.locator('#result-panel').isVisible(), false);
  assert.match(await panel.locator('[data-ui="status"]').innerText(), /Paused/, 'History previews must preserve the live activity label.');
  assert.equal(await panel.locator('[data-ui="viewing"]').isVisible(), true);
  await panel.locator('[data-ui="live"]').click();
  await page.waitForFunction(() => document.querySelector('#file-name').textContent === 'R1.stl');
  await page.locator('#result-panel').waitFor({state: 'visible'});
  assert.equal(await page.locator('#result-force').innerText(), '2.268 kg', 'History replay must preserve the unit preference.');
  await panel.locator('[data-ui="timeline"] li[data-sequence="2"] button').filter({hasText: 'Try a copy from here'}).click();
  await page.waitForFunction(original => document.querySelector('[data-ui="id"]').textContent !== original, id);
  const branch = (await panel.locator('[data-ui="id"]').textContent()).trim();
  assert.equal(await panel.locator('[data-ui="status"]').innerText(), 'Saved work');
  assert.equal(await panel.locator('[data-ui="continue"]').isVisible(), false);
  assert.notEqual(branch, id);
  await page.waitForFunction(() => document.querySelector('#file-name').textContent === 'R0.stl');
  await panel.locator('[data-ui="sessions"]').selectOption(id);
  await page.waitForFunction(original => document.querySelector('[data-ui="id"]').textContent === original, id);
  await panel.locator('[data-ui="continue"]').click();
  await panel.locator('[data-ui="close"]').click();
  assert.equal(await panel.isVisible(), false);
  assert.equal((await command('get', id)).value.status, 'running', 'Closing the window does not pause the connected agent.');
  await command('complete', id, '--summary', 'Candidate ready for review. Numerical behavior recorded; fit needs physical checking.');
  await page.waitForFunction(() => document.querySelector('#open-agent').textContent.includes('Review ready'));
  assert.match(await page.locator('#notice-text').innerText(), /Candidate ready for review/);
  assert.equal(await panel.isVisible(), false, 'Live completion notifications must not reopen the window.');
  await page.locator('#open-agent').click();
  assert.equal((await panel.locator('[data-ui="id"]').textContent()).trim(), id, 'Reopening retains the same session.');
  assert.match(await panel.locator('[data-ui="status"]').innerText(), /Ready for your review/);
  assert.match(await panel.locator('[data-ui="timeline"]').innerText(), /Candidate ready for review/);
  assert.equal(await page.locator('#file-name').innerText(), 'R1.stl');
  assert.equal(await page.locator('#result-force').innerText(), '2.268 kg');
  await panel.locator('[data-ui="maximize"]').click();
  assert.equal(await panel.evaluate(element => element.classList.contains('maximized')), true);
  await mkdir(shots, {recursive: true});
  await page.screenshot({path: resolve(shots, 'agent-session-desktop.png'), fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  await page.screenshot({path: resolve(shots, 'agent-session-mobile.png'), fullPage: true});
  await panel.locator('[data-ui="maximize"]').click();
  const mobileCard = await panel.boundingBox();
  assert.ok(mobileCard.x >= 0 && mobileCard.y >= 0 && mobileCard.x + mobileCard.width <= 390 && mobileCard.y + mobileCard.height <= 844);
  await page.locator('#close-notice').click(); // The review notification covers the lower corner on a narrow screen.
  // Trusted touch events exercise the same resize grip on a narrow viewport.
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Emulation.setTouchEmulationEnabled', {enabled: true});
  const grip = await panel.locator('[data-ui="resize"]').boundingBox();
  const touch = {x: grip.x + grip.width / 2, y: grip.y + grip.height / 2, id: 1};
  await cdp.send('Input.dispatchTouchEvent', {type: 'touchStart', touchPoints: [touch]});
  await cdp.send('Input.dispatchTouchEvent', {type: 'touchMove', touchPoints: [{...touch, x: touch.x - 40, y: touch.y - 60}]});
  await cdp.send('Input.dispatchTouchEvent', {type: 'touchEnd', touchPoints: []});
  const touchResized = await panel.boundingBox();
  closeSize(touchResized.width, Math.max(300, mobileCard.width - 40));
  closeSize(touchResized.height, Math.max(280, mobileCard.height - 60));
  await cdp.detach();
  await resizeCard('se', 1000, 1000);
  const boundedCard = await panel.boundingBox();
  assert.ok(boundedCard.x + boundedCard.width <= 382 && boundedCard.y + boundedCard.height <= 836);
  await panel.locator('[data-ui="collapse"]').click();
  await page.screenshot({path: resolve(shots, 'agent-session-pill.png')});
  await page.setViewportSize({width: 320, height: 600});
  const smallPill = await panel.boundingBox();
  assert.ok(smallPill.x >= 0 && smallPill.y >= 0 && smallPill.x + smallPill.width <= 320 && smallPill.y + smallPill.height <= 600);
  const exited = new Promise(resolve => server.once('exit', resolve));
  server.kill('SIGKILL'); // Simulate a shutdown; graceful shutdown keeps SSE clients open.
  await page.waitForFunction(() => document.querySelector('[data-ui="connection"]').textContent.includes('disconnected'));
  assert.match(await panel.locator('[data-ui="status"]').textContent(), /Saved work/);
  assert.equal(await panel.locator('[data-ui="continue"]').isVisible(), false);
  await exited;
  await startServer(new URL(url).port);
  await page.waitForFunction(() => document.querySelector('[data-ui="connection"]').textContent.includes('MakerSim updates connected'));
  await page.waitForFunction(() => document.querySelector('[data-ui="status"]').textContent.startsWith('Saved work'));
  await page.locator('#open-agent').click();
  assert.equal(await panel.locator('[data-ui="managed"]').isVisible(), false, 'A service restart requires fresh agent evidence.');
  assert.equal(await page.locator('#result-force').innerText(), '2.268 kg');
  await command('agent', id, '--name', 'Studio helper'); // An unrelated compatible app; viewing/feedback is the default.
  await page.waitForFunction(() => document.querySelector('[data-ui="status"]').textContent === 'Studio helper connected');
  assert.equal(await panel.locator('[data-ui="managed"]').isVisible(), false);
  await command('authorize', id, runIds.get(id), 'start', '--human-instruction', 'Start a new run using this saved bracket and its history.');
  await panel.locator('[data-ui="intervene"] textarea').fill('Keep the mounting holes exactly where they were.');
  await panel.locator('[data-ui="intervene"] button').click();
  await page.waitForFunction(() => document.querySelector('[data-ui="timeline"]').textContent.includes('Keep the mounting holes exactly where they were.'));
  assert.equal((await command('get', id)).value.status, 'running', 'A feedback-only window must not claim to pause an external agent.');
  await command('control', id, 'pause', '--message', 'Human paused from the agent app.');
  await page.waitForFunction(() => document.querySelector('[data-ui="status"]').textContent.includes('Paused'));
  assert.equal(await panel.locator('[data-ui="continue"]').isVisible(), false);
  await command('authorize', id, runIds.get(id), 'resume', '--human-instruction', 'Continue from my agent app.');
  await command('complete', id, '--summary', 'Saved bracket ready for review; physical fit still needs checking.');
  await command('signal', id, runIds.get(id), 'disconnect');
  await page.waitForFunction(() => document.querySelector('[data-ui="status"]').textContent === 'Saved work · ready for review');
  await page.screenshot({path: resolve(shots, 'agent-session-saved.png')});
  await page.setViewportSize({width: 1440, height: 1060});
  await panel.locator('[data-ui="maximize"]').click();
  assert.equal(await page.locator('#result-panel').isVisible(), true);
  const createScratch = async objective => {
    await panel.locator('[data-ui="sessions"]').selectOption('');
    await panel.locator('[data-ui="create"]').waitFor({state: 'visible'});
    await panel.locator('[name="source"]').selectOption('scratch');
    await panel.locator('[name="objective"]').fill(objective);
    await panel.locator('[data-ui="create"] button[type="submit"]').click();
    await page.waitForFunction(() => document.querySelector('[data-ui="id"]').textContent.length === 32);
    return (await panel.locator('[data-ui="id"]').textContent()).trim();
  };
  const assertEmptyViewport = async () => {
    await page.waitForFunction(() => !document.querySelector('#empty-view').hidden &&
      document.querySelector('#file-name').textContent === '' && document.querySelector('#result-panel').hidden);
    assert.equal(await page.locator('#file-summary').isVisible(), false);
    assert.equal(await page.locator('#heatmap-legend').isVisible(), false);
    assert.equal(await page.locator('#hold-count').innerText(), 'No holds yet');
    assert.equal(await page.locator('#pull-count').innerText(), 'No pull yet');
    assert.equal(await page.locator('#simulate').isDisabled(), true);
    assert.equal(await page.locator('#force-unit').inputValue(), 'kg', 'Clearing a session preserves the chosen load unit.');
  };
  const scratch = await createScratch('Build a different part from scratch.');
  assert.notEqual(scratch, id);
  assert.equal((await command('get', scratch)).value.revision, null);
  await assertEmptyViewport();
  await panel.locator('[data-ui="maximize"]').click();
  await panel.locator('[data-ui="collapse"]').click();
  await page.screenshot({path: resolve(shots, 'agent-session-empty.png')});
  await page.locator('#open-agent').click();
  await panel.locator('[data-ui="maximize"]').click();

  // An old geometry upload is already in flight when another session starts.
  // Its late response must never restore the old part or simulation.
  let releaseUpload, uploadStarted;
  const uploadGate = new Promise(resolve => { releaseUpload = resolve; });
  const uploadReady = new Promise(resolve => { uploadStarted = resolve; });
  await page.route('**/api/models', async route => {
    const response = await route.fetch();
    uploadStarted();
    await uploadGate;
    await route.fulfill({response}).catch(() => {}); // The obsolete browser request may already be aborted.
  });
  await panel.locator('[data-ui="sessions"]').selectOption(id);
  await uploadReady;
  const nextScratch = await createScratch('A fresh session must not inherit a delayed old preview.');
  assert.notEqual(nextScratch, scratch);
  await assertEmptyViewport();
  releaseUpload();
  await page.unroute('**/api/models');
  await page.waitForFunction(() => document.querySelector('#busy-overlay').hidden);
  await assertEmptyViewport();
  assert.equal(await panel.locator('[data-ui="error"]').isVisible(), false);

  // A selected new part becomes the next session's only baseline, with no
  // previous part's dimensions, fixtures or result retained.
  const replacement = Buffer.from(await readFile(resolve(root, 'backend/data/backpack-bracket.stl')));
  for (let face = 0; face < replacement.readUInt32LE(80); face++) {
    for (let coordinate = 0; coordinate < 9; coordinate++) {
      const offset = 84 + face*50 + 12 + coordinate*4;
      replacement.writeFloatLE(replacement.readFloatLE(offset)*.5, offset);
    }
  }
  await page.locator('#stl-file').setInputFiles({name: 'new-half-size-bracket.stl', mimeType: 'model/stl', buffer: replacement});
  await page.waitForFunction(() => document.querySelector('#file-name').textContent === 'new-half-size-bracket.stl');
  await panel.locator('[data-ui="sessions"]').selectOption('');
  await panel.locator('[name="source"]').selectOption('copy');
  await panel.locator('[name="objective"]').fill('Work only on this newly selected smaller part.');
  await panel.locator('[data-ui="create"] button[type="submit"]').click();
  await page.waitForFunction(previous => document.querySelector('[data-ui="id"]').textContent.length === 32 &&
    document.querySelector('[data-ui="id"]').textContent !== previous, nextScratch);
  await page.waitForFunction(() => document.querySelector('#file-name').textContent === 'R0.stl' &&
    document.querySelector('#file-dimensions').textContent === '39.5 × 8.0 × 25.0 mm');
  assert.equal(await page.locator('#result-panel').isVisible(), false);
  assert.equal(await page.locator('#hold-count').innerText(), 'No holds yet');
  assert.equal(await page.locator('#pull-count').innerText(), 'No pull yet');
  await page.locator('#stl-file').setInputFiles({name: 'different-viewport-part.stl', mimeType: 'model/stl',
    buffer: await readFile(resolve(root, 'backend/data/backpack-bracket.stl'))});
  await page.waitForFunction(() => document.querySelector('#file-name').textContent === 'different-viewport-part.stl');
  await panel.locator('[data-ui="live"]').click();
  await page.waitForFunction(() => document.querySelector('#file-name').textContent === 'R0.stl' &&
    document.querySelector('#file-dimensions').textContent === '39.5 × 8.0 × 25.0 mm');
  await panel.locator('[data-ui="sessions"]').selectOption(nextScratch);
  await assertEmptyViewport();
  assert.deepEqual(errors, []);
  console.log('Live session browser checks passed: size preserved across focus/tab switches and temporary viewport changes, hidden until opened, close/reopen in card/pill/full screen, closed-window notifications and session continuity, clean new-session viewports, cancelled old previews, selected-part baselines, saved work versus named live agents, optional controls, agent-app start/resume, shutdown/reconnect, feedback-only mode, mouse/keyboard/touch resizing, SSE, CAD snapshots, simulation, units, replay and branches.');
} catch (error) {
  await mkdir(shots, {recursive: true});
  await page.screenshot({path: resolve(shots, 'agent-session-error.png'), fullPage: true});
  console.error('Session browser state:', await page.evaluate(() => ({
    status: document.querySelector('[data-ui="status"]')?.textContent,
    error: document.querySelector('[data-ui="error"]')?.textContent,
    notice: document.querySelector('#notice-text')?.textContent,
    viewport: [innerWidth, innerHeight],
    box: document.querySelector('.agent-window')?.getBoundingClientRect().toJSON(),
  })), errors);
  throw error;
} finally {
  await browser.close(); server.kill(); await rm(directory, {recursive: true, force: true});
}
