/* What the voice path relays, and what it drops.
 *
 * "open" was added to the server and to app.js and not to the switch in
 * live.js, which had no default case. Every spoken "open YouTube" was
 * dropped there in silence -- while the server had already told the
 * model it had opened something, so JARVIS said so out loud and nothing
 * happened. Three rounds of "it says it did it and I can't see it".
 *
 * This drives live.js's own handler with the frames the server actually
 * sends, rather than opening a microphone.
 */
import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:8099';
const out = [];
let bad = false;
const fail = (m) => { console.error('FAIL: ' + m); process.exitCode = 1; bad = true; };
const ok = (m) => { if (!bad) out.push(m); bad = false; };

const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
const ctx = await browser.newContext({ viewport: { width: 1180, height: 820 } });
const page = await ctx.newPage();
await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#app:not(.hidden)', { timeout: 15000 });

// Drive live.js's OWN handler, not app.js's listener.
//
// The first version of this check dispatched `jarvis:offer` straight
// from the page, which bypasses live.js entirely -- it was testing
// app.js and would have passed with the relay deleted. Verified by
// deleting it: the check stayed green. So the socket is stubbed instead,
// the real onmessage is captured, and frames go in the way the server
// sends them.
await page.evaluate(() => {
  window.__frames = [];
  const Real = window.WebSocket;
  window.WebSocket = function (url) {
    const fake = {
      url, readyState: 1, bufferedAmount: 0,
      send() {}, close() { this.readyState = 3; },
      addEventListener() {}, removeEventListener() {},
    };
    window.__ws = fake;
    // live.js assigns onopen/onmessage after construction.
    setTimeout(() => { if (fake.onopen) fake.onopen(); }, 0);
    return fake;
  };
  window.WebSocket.OPEN = 1;
  window.__RealWebSocket = Real;

  // The microphone is not the subject; stub it so startLive() proceeds.
  navigator.mediaDevices = navigator.mediaDevices || {};
  navigator.mediaDevices.getUserMedia = async () => ({
    getTracks: () => [{ stop() {} }],
  });
});

await page.click('#micBtn').catch(() => {});
await page.waitForFunction(() => window.__ws && window.__ws.onmessage,
                           { timeout: 8000 })
  .catch(() => fail('the voice socket never got a message handler'));

// The frame the server sends when he says "open YouTube" out loud.
await page.evaluate(() => {
  window.__ws.onmessage({ data: JSON.stringify({
    type: 'open', url: 'https://www.youtube.com', site: 'YouTube',
    query: null, said: 'Opening YouTube.' }) });
});
await page.waitForTimeout(600);

const link = await page.$('.msg.jarvis a.btn');
if (!link) fail('a spoken "open YouTube" put nothing on screen');
else {
  const href = await link.getAttribute('href');
  if (!href || !href.includes('youtube.com'))
    fail(`the spoken open produced ${href}`);
  else ok('a spoken "open YouTube" reaches the page and leaves a link');
}

const said = await page.textContent('.msg.jarvis .body').catch(() => '');
if (!/opening youtube/i.test(said || ''))
  fail(`the spoken open said: ${said}`);
else ok('and says what it is doing');

// --- every type the server can send must be relayed ----------------------
//
// The real guard: live.js must not silently swallow a frame somebody
// added at the other end. An unknown type is relayed AND warned about.
const warnings = [];
page.on('console', (m) => { if (m.type() === 'warning') warnings.push(m.text()); });

const relayed = await page.evaluate(() => new Promise((resolve) => {
  let seen = null;
  const listen = (e) => { seen = e.detail.type; };
  document.addEventListener('jarvis:offer', listen);
  // Through live.js's switch, as a frame it has never heard of.
  window.__ws.onmessage({ data: JSON.stringify({ type: 'something_new_2027' }) });
  setTimeout(() => {
    document.removeEventListener('jarvis:offer', listen);
    resolve(seen);
  }, 250);
}));
if (relayed !== 'something_new_2027')
  fail('an unfamiliar frame does not reach the page at all');
else ok('an unfamiliar frame is still delivered rather than dropped');

// --- choosing a browser only where that is possible ----------------------
for (const [label, ua, expected] of [
  ['iPad', 'Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 ' +
           '(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1',
   'googlechromes://'],
  ['Windows laptop', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
                     '(KHTML, like Gecko) Chrome/140.0 Safari/537.36', 'https://'],
]) {
  const other = await browser.newContext({ userAgent: ua });
  const p2 = await other.newPage();
  await p2.goto(BASE, { waitUntil: 'domcontentloaded' });
  await p2.waitForSelector('#app:not(.hidden)', { timeout: 15000 });
  // app.js's listener is the subject here, not the relay, so the frame
  // is dispatched directly -- what is being measured is which address
  // the page builds for this device.
  await p2.evaluate(() => {
    document.dispatchEvent(new CustomEvent('jarvis:offer', {
      detail: { type: 'open', url: 'https://www.youtube.com', site: 'YouTube',
                browser: 'chrome', said: 'Opening YouTube.' },
    }));
  });
  await p2.waitForTimeout(500);
  const href = await p2.getAttribute('.msg.jarvis a.btn', 'href').catch(() => null);
  if (!href || !href.startsWith(expected))
    fail(`on a ${label}, "in Chrome" produced ${href} (wanted ${expected}…)`);
  else ok(`on a ${label}, "in Chrome" produces ${expected}…`);

  if (label === 'Windows laptop') {
    const why = await p2.textContent('.msg.jarvis .note').catch(() => '');
    if (!/decides which browser/i.test(why || ''))
      fail('a computer is not told that it, not JARVIS, picks the browser');
    else ok('and says plainly that the computer picks, not JARVIS');
  }
  await other.close();
}

// --- leaving JARVIS and coming back ---------------------------------------
//
// Opening Spotify backgrounds the tab, iOS suspends it, the socket dies
// and stopLive() runs. Nothing used to start it again, so coming back
// found a dead microphone. Listening WHILE away is not fixable on a web
// page and is not what this checks; coming back is.
{
  const resumed = await page.evaluate(async () => {
    // Put it genuinely into the listening state first. Without a "ready"
    // frame LIVE.active is never set, so there is nothing to resume and
    // the check passes for the wrong reason -- which is how the first
    // version of this failed.
    try {
      window.__ws.onmessage({ data: JSON.stringify({
        type: 'ready', input_rate: 16000, output_rate: 24000,
        voice: 'test', model: 'test' }) });
    } catch (e) { /* capture needs real audio nodes; the flag is set first */ }
    await new Promise((r) => setTimeout(r, 100));
    const before = window.__live ? window.__live.active : true;

    // Go away: the socket closes, exactly as a suspended tab does.
    Object.defineProperty(document, 'hidden',
                          { configurable: true, get: () => true });
    document.dispatchEvent(new Event('visibilitychange'));
    if (window.__ws && window.__ws.onclose) window.__ws.onclose({ code: 1006 });

    const socketAfterLeaving = window.__ws;

    // Come back.
    Object.defineProperty(document, 'hidden',
                          { configurable: true, get: () => false });
    document.dispatchEvent(new Event('visibilitychange'));
    await new Promise((r) => setTimeout(r, 400));

    return { before, reconnected: window.__ws !== socketAfterLeaving };
  });

  if (!resumed.before) fail('the voice session never reached the listening state');
  else if (!resumed.reconnected)
    fail('coming back to the tab did not restart listening — the mic stays ' +
         'dead and the button says otherwise');
  else ok('leaving for another app and coming back starts listening again');
}

// --- and it says so before he finds out -----------------------------------
{
  const p3 = await ctx.newPage();
  await p3.goto(BASE, { waitUntil: 'domcontentloaded' });
  await p3.waitForSelector('#app:not(.hidden)', { timeout: 15000 });
  await p3.evaluate(() => {
    document.dispatchEvent(new CustomEvent('jarvis:offer', {
      detail: { type: 'open', url: 'https://www.youtube.com', site: 'YouTube',
                said: 'Opening YouTube.' },
    }));
  });
  await p3.waitForTimeout(400);
  const note = await p3.textContent('.msg.jarvis .note').catch(() => '');
  if (!/cannot hear you/i.test(note || ''))
    fail('nothing warns him that JARVIS goes deaf while he is in the app');
  else if (!/starts listening again/i.test(note || ''))
    fail('it does not say that coming back fixes it');
  else ok('it says plainly that it cannot hear him while he is away, ' +
          'and that coming back restores it');
  await p3.close();
}

console.log(out.map((l) => '  ' + l).join('\n'));
await browser.close();
