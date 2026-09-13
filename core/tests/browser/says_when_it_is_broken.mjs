/* The page telling you it is broken, instead of looking fine.
 *
 * Sagar moved JARVIS to his own server, saw the dashboard render with
 * every tab in place, and nothing worked. There was no way for him to
 * tell me why, because there was nothing on the screen to read: thirteen
 * calls checked `res.ok`, returned quietly when it was false, and left a
 * healthy-looking page with dead buttons.
 *
 * A signed-out session, a container running an older image, and a server
 * that is not running at all are three completely different problems
 * that all used to look exactly like that. This check makes each one say
 * which it is.
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
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#app:not(.hidden)', { timeout: 15000 });

const banner = async () => {
  const el = await page.$('#trouble');
  const hidden = await el.evaluate((n) => n.hidden);
  const text = await page.textContent('#troubleText');
  return { hidden, text: (text || '').trim() };
};

// Nothing is wrong yet.
if (!(await banner()).hidden) fail('the banner is showing with nothing wrong');
else ok('nothing on screen while the back end is answering');

// Waiting for the banner to be *visible* is not enough: it is usually
// still up from the previous scenario, so the wait returns instantly and
// reads the old message. Wait for the text to actually change.
async function whenTheServerDoes(what, verb) {
  const before = (await banner()).text;
  await page.unroute('**/v1/**').catch(() => {});
  await page.route('**/v1/**', what);
  await page.click('button[data-view="home"]');
  await page.click('button[data-view="build"]');
  await page.waitForFunction(
    (was) => {
      const n = document.getElementById('trouble');
      const said = document.getElementById('troubleText');
      return n && !n.hidden && said && said.textContent.trim() !== was;
    },
    before,
    { timeout: 8000 },
  ).catch(() => fail(`nothing new was shown when the server ${verb}`));
  return (await banner()).text;
}

// --- signed out -----------------------------------------------------------
let said = await whenTheServerDoes(
  (route) => route.fulfill({ status: 401, body: '{"detail":"no"}' }),
  'returns 401');
if (!/signed out/i.test(said)) fail(`a 401 said: ${said}`);
else if (!/sign in again/i.test(said)) fail('it does not say what to do about it');
else ok('a signed-out session says so, and says to sign in again');
if (!/COOKIE_SECURE/.test(said))
  fail('it does not mention the cookie, which is the usual cause over http');
else ok('and names the cookie setting, which is why sign-in often will not stick');

// --- an older image than the page -----------------------------------------
said = await whenTheServerDoes(
  (route) => route.fulfill({ status: 404, body: '{"detail":"no"}' }),
  'returns 404');
if (!/older build/i.test(said)) fail(`a 404 said: ${said}`);
else if (!/docker compose up -d --build/.test(said))
  fail('it does not give the command that fixes it');
else ok('a missing route says the build is older than the page, with the fix');

// --- the server is simply not there ---------------------------------------
said = await whenTheServerDoes((route) => route.abort(), 'cannot be reached');
if (!/not answering/i.test(said)) fail(`an unreachable server said: ${said}`);
else if (!/docker compose logs/.test(said))
  fail('it does not say where to look');
else ok('an unreachable server says so, and where the log is');

// --- a crash --------------------------------------------------------------
said = await whenTheServerDoes(
  (route) => route.fulfill({ status: 500, body: 'boom' }), 'crashes');
if (!/error/i.test(said) || !/logs/.test(said)) fail(`a 500 said: ${said}`);
else ok('a server error points at the log rather than staying silent');

// --- and it goes away again -----------------------------------------------
await page.unroute('**/v1/**');
await page.click('button[data-view="home"]');
await page.click('button[data-view="build"]');
await page.waitForFunction(
  () => { const n = document.getElementById('trouble'); return n && n.hidden; },
  { timeout: 8000 },
).catch(() => fail('the banner stayed up after the back end recovered'));
ok('it clears itself once JARVIS answers again');

// --- it must not shout on the sign-in screen ------------------------------
const fresh = await ctx.newPage();
await fresh.addInitScript(() => {
  // Make the page believe it is signed out for its whole life.
  const real = window.fetch;
  window.fetch = (...args) =>
    String(args[0]).includes('/v1/')
      ? Promise.resolve(new Response('{"detail":"no"}', { status: 401 }))
      : real(...args);
});
await fresh.goto(BASE, { waitUntil: 'domcontentloaded' });
await fresh.waitForTimeout(1500);
const gateUp = await fresh.$eval('#gate', (n) => !n.classList.contains('hidden'));
const shouting = await fresh.$eval('#trouble', (n) => !n.hidden);
if (gateUp && shouting)
  fail('it puts a failure banner on the sign-in screen, which is working correctly');
else ok('it stays quiet on the sign-in screen, where a 401 is the design');

if (errors.length) fail('the page logged errors: ' + errors.join(' | '));
console.log(out.map((l) => '  ' + l).join('\n'));
await browser.close();
