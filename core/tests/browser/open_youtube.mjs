/* "Open YouTube" opening YouTube.
 *
 * The interesting part is not the happy path. window.open() called from
 * inside a fetch callback is not a user gesture, and popup blockers --
 * iOS Safari most strictly -- refuse it. So the page has to notice it was
 * blocked and offer a button instead, because a tap on that button IS a
 * gesture and always works.
 *
 * A version that only tried window.open() would work perfectly on this
 * machine and do nothing at all on the iPad, which is the device it is
 * for.
 */
import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:8099';
const out = [];
let bad = false;
const fail = (m) => { console.error('FAIL: ' + m); process.exitCode = 1; bad = true; };
const ok = (m) => { if (!bad) out.push(m); bad = false; };

const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
const ctx = await browser.newContext({ viewport: { width: 1180, height: 820 } });

// Stub the destination. This machine has no route to youtube.com, so a
// real navigation lands on a browser error page and page.url() reports
// chrome-error://, telling us nothing about whether JARVIS asked for the
// right address. What is being checked is the mechanism and the URL, not
// whether YouTube is up.
await ctx.route('**://*.youtube.com/**', (route) =>
  route.fulfill({ status: 200, contentType: 'text/html', body: '<h1>stub</h1>' }));
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#app:not(.hidden)', { timeout: 15000 });

async function say(text) {
  await page.fill('#msg', text);
  await page.click('#sendBtn');
  await page.waitForFunction(
    (n) => document.querySelectorAll('.msg.jarvis').length > n,
    await page.$$eval('.msg.jarvis', (n) => n.length),
    { timeout: 20000 },
  );
}

// --- the tab actually opens -----------------------------------------------
const opened = ctx.waitForEvent('page', { timeout: 10000 }).catch(() => null);
await say('Open YouTube');
const tab = await opened;
if (!tab) fail('saying "Open YouTube" did not open a tab');
else if (!tab.url().includes('youtube.com'))
  fail(`it opened ${tab.url()} instead of YouTube`);
else ok('"Open YouTube" opens YouTube in a new tab');
if (tab) await tab.close();

// --- it says so, and says it honestly -------------------------------------
const said = await page.textContent('.msg.jarvis:last-child .body');
if (!/opening youtube/i.test(said || '')) fail(`it said: ${said}`);
else if (/opened/i.test(said || ''))
  fail('it claims the tab opened; it cannot know that, and blockers exist');
else ok('it says "opening", which is all it can honestly claim');

// --- searching ------------------------------------------------------------
const searched = ctx.waitForEvent('page', { timeout: 10000 }).catch(() => null);
await say('search youtube for lofi beats');
const two = await searched;
if (!two) fail('a spoken search did not open anything');
else if (!two.url().includes('search_query=lofi+beats'))
  fail(`the search went to ${two.url()}`);
else ok('"search youtube for lofi beats" lands on the results page');
if (two) await two.close();

// --- blocked popups fall back to a button ---------------------------------
const blocked = await ctx.newPage();
await blocked.addInitScript(() => { window.open = () => null; });
await blocked.goto(BASE, { waitUntil: 'domcontentloaded' });
await blocked.waitForSelector('#app:not(.hidden)', { timeout: 15000 });
await blocked.fill('#msg', 'Open YouTube');
await blocked.click('#sendBtn');
await blocked.waitForSelector('.msg.jarvis button.btn', { timeout: 20000 })
  .catch(() => fail('a blocked popup left nothing on screen to tap'));
const label = await blocked.textContent('.msg.jarvis button.btn').catch(() => '');
if (!/open youtube/i.test(label || ''))
  fail(`the fallback button said: ${label}`);
else ok('a blocked tab becomes a button that names where it goes');

const why = await blocked.textContent('.msg.jarvis .note').catch(() => '');
if (!/blocked/i.test(why || ''))
  fail('it does not say why the button is there');
else ok('and says the browser blocked it, rather than looking like a bug');

// --- ordinary chat is untouched -------------------------------------------
const nothing = ctx.waitForEvent('page', { timeout: 3000 }).catch(() => null);
await say('What is 2+2?');
if (await nothing) fail('an ordinary message opened a tab');
else ok('ordinary messages open nothing');

if (errors.length) fail('the page logged errors: ' + errors.join(' | '));
console.log(out.map((l) => '  ' + l).join('\n'));
await browser.close();
