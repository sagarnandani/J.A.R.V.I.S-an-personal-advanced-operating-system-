/* This tab, as a pair of hands.
 *
 * The only sidecar an iPad can be. Everything here needs a real browser
 * to mean anything: pairing from the page, polling while visible,
 * stopping when hidden, and -- the one that matters -- refusing a job
 * the device was never paired with, even when the server asks for it.
 *
 * Run at iPad size, because that is the device this exists for.
 */
import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:8099';
const out = [];
let failed = false;
const fail = (m) => { console.error('FAIL: ' + m); process.exitCode = 1; failed = true; };
const ok = (m) => { if (!failed) out.push(m); failed = false; };

const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
// iPad portrait. The device this is for.
const ctx = await browser.newContext({ viewport: { width: 820, height: 1180 } });
const page = await ctx.newPage();
const errors = [];
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', (e) => errors.push(String(e)));

// Speech is not available in headless Chromium, so the page's own voice
// is stubbed. What is being tested is the sidecar loop, not the speaker.
await page.addInitScript(() => {
  window.__said = [];
  window.say = (t) => window.__said.push(t);
});

await page.goto(BASE, { waitUntil: 'networkidle' });
await page.waitForSelector('#app:not(.hidden)', { timeout: 15000 });
await page.click('button[data-view="build"]');
await page.waitForSelector('#sidecarThis', { timeout: 10000 });
await page.waitForFunction(
  () => document.getElementById('sidecarThis').innerHTML.length > 0,
  null, { timeout: 10000 });
ok('the Build tab offers this device as a pair of hands');

// --- it says what it is before he taps ------------------------------------
const before = await page.locator('#sidecarThis').textContent();
if (!/speak|show|link/i.test(before))
  fail('it does not say what this device would actually be able to do');
ok('it says what this device could do before pairing it');

// --- one tap, no code -----------------------------------------------------
await page.click('#sidecarPairBtn');
await page.waitForFunction(
  () => (document.getElementById('sidecarThis').textContent || '')
    .includes('Right now'), null, { timeout: 10000 });
ok('paired in one tap, with no code to copy');

const paired = await (await fetch(BASE + '/v1/sidecars')).json();
const mine = (paired.sidecars || []).find((s) => s.kind === 'browser');
if (!mine) fail('the server has no browser sidecar after pairing');
else {
  if (mine.capabilities.includes('terminal'))
    fail('a browser was granted terminal');
  ok(`server sees it as ${mine.name}: ${mine.capabilities.join(', ')}`);
}

// --- it does what it was paired for ---------------------------------------
const spoke = await page.evaluate(async () =>
  await sidecarRunOne({ capability: 'speak', arguments: { text: 'Deadline is April 30.' } },
                      ['speak', 'notify', 'browser']));
if (!spoke.ok) fail(`it could not speak: ${spoke.error}`);
const heard = await page.evaluate(() => window.__said);
if (!heard.includes('Deadline is April 30.'))
  fail(`it reported speaking but nothing was said: ${JSON.stringify(heard)}`);
ok('it speaks when asked, through the dashboard\'s own voice');

// A link put in front of him, never a tab opened behind his back.
const offered = await page.evaluate(async () =>
  await sidecarRunOne({ capability: 'browser', arguments: { url: 'https://example.com/x' } },
                      ['speak', 'notify', 'browser']));
if (!offered.ok) fail(`it could not offer a link: ${offered.error}`);
if (offered.result.opened !== false)
  fail('it claims to have opened a tab; a page cannot do that without a tap');
const link = await page.locator('#sidecarMessages a.btn').first();
if (!(await link.isVisible())) fail('no tappable link was put in front of him');
if (await link.getAttribute('href') !== 'https://example.com/x')
  fail('the link does not go where JARVIS asked');
ok('an "open" becomes a link you tap, not a tab opened behind your back');

// And not every scheme.
const bad = await page.evaluate(async () =>
  await sidecarRunOne({ capability: 'browser', arguments: { url: 'file:///etc/passwd' } },
                      ['browser']));
if (bad.ok) fail('it offered a file:// address');
ok('it will only offer http and https');

const said = await page.evaluate(async () => {
  const saved = JSON.parse(localStorage.getItem('jarvis.sidecar'));
  return { name: saved.name, capabilities: saved.capabilities,
           hasToken: Boolean(saved.token) };
});
if (!said.hasToken) fail('no token was saved in the browser');
if (said.capabilities.includes('terminal')) fail('the tab thinks it may use a terminal');
ok(`the tab holds its own grant: ${said.capabilities.join(', ')}`);

// --- the refusal, which is the whole point --------------------------------
const refused = await page.evaluate(async () =>
  // Asked to do something it was never paired with, exactly as a
  // tampered server would.
  await sidecarRunOne({ capability: 'terminal', arguments: { argv: ['id'] } },
                      ['speak', 'notify', 'browser']));
if (refused.ok) fail('the tab ran a capability it was never paired with');
if (!/was not paired with/.test(refused.error))
  fail(`the refusal does not say why: ${refused.error}`);
ok('it refuses what it was not paired with, whatever the server asks');

// --- and it stops when the tab goes away ----------------------------------
const polling = await page.evaluate(() => sidecarTimer !== null);
if (!polling) fail('it is not polling while the tab is visible');
ok('polling while the tab is on screen');

// A hidden tab is a tab iOS is about to suspend. Claiming to listen
// while the screen is off would be a lie he cannot check.
await page.evaluate(() => {
  Object.defineProperty(document, 'hidden', { value: true, configurable: true });
  document.dispatchEvent(new Event('visibilitychange'));
});
await page.waitForFunction(() => sidecarTimer === null, null, { timeout: 5000 })
  .catch(() => fail('it kept polling after the tab went to the background'));
const backgroundText = await page.locator('#sidecarThis').textContent();
if (!/not listening/i.test(backgroundText))
  fail('it does not SAY it has stopped listening in the background');
ok('stops, and says so, when the tab goes to the background');

const screenshot = process.argv[2] || 'browser-sidecar.png';
await page.screenshot({ path: screenshot, fullPage: true });

const real = errors.filter((e) => !/Failed to load resource|speechSynthesis/.test(e));
if (real.length) fail('console errors: ' + real.join(' | '));

console.log(out.map((l) => '  ok  ' + l).join('\n'));
await browser.close();
