const session = document.querySelector('.session');
const status = document.querySelector('#status');
const availability = document.querySelector('#availability');
const launch = document.querySelector('#launch');
async function check() {
  let online = false;
  try {
    const response = await fetch('https://makersim-playground.rawcastdigital.com/api/health', {cache:'no-store',credentials:'omit',signal:AbortSignal.timeout(7000)});
    const health = response.ok ? await response.json() : null;
    online = health?.status === 'ready' && health?.mode === 'hosted';
  } catch { /* The landing page remains useful while Legion is offline. */ }
  session.classList.toggle('online', online);
  status.textContent = online ? 'PLAYGROUND ONLINE' : 'PLAYGROUND OFFLINE';
  availability.textContent = online ? 'The testing server is available. Start with the example or a non-sensitive part. This preview has limited capacity; please retry later if it is busy.' : 'The testing server is currently unavailable. Request a testing session through ShftState and we’ll arrange a time to try your part.';
  launch.hidden = !online;
}
check();
setInterval(check, 30000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });
