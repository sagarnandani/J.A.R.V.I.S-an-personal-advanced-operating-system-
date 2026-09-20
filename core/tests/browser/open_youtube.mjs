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

// --- a browser that refuses to open anything -------------------------------
//
// Two shapes of refusal, and the second is the one that mattered.
// window.open returning null is the textbook blocked popup. Returning a
// truthy Window that never navigates is what iOS Safari does, and it
// defeats every check for the first. Both must end with a link.
for (const [how, stub] of [
  ['refusing outright', () => { window.open = () => null; }],
  ['pretending it worked',
   () => { window.open = () => ({ closed: false, focus() {} }); }],
]) {
  const blocked = await ctx.newPage();
  await blocked.addInitScript(stub);
  await blocked.goto(BASE, { waitUntil: 'domcontentloaded' });
  await blocked.waitForSelector('#app:not(.hidden)', { timeout: 15000 });
  await blocked.fill('#msg', 'Open YouTube');
  await blocked.click('#sendBtn');

  const link = await blocked.waitForSelector('.msg.jarvis a.btn', { timeout: 20000 })
    .catch(() => null);
  if (!link) {
    fail(`a browser ${how} left nothing on screen to tap`);
  } else {
    const label = (await link.textContent()) || '';
    const href = (await link.getAttribute('href')) || '';
    if (!/open youtube/i.test(label)) fail(`the link said: ${label}`);
    else if (!href.includes('youtube.com')) fail(`the link points at ${href}`);
    else ok(`a browser ${how} still leaves a link that names where it goes`);

    const why = await blocked.textContent('.msg.jarvis .note').catch(() => '');
    if (!/tap this/i.test(why || ''))
      fail('nothing tells him to tap it');
    else ok('and tells him to tap it, rather than looking like a dead end');
  }
  await blocked.close();
}

// --- ordinary chat is untouched -------------------------------------------
const nothing = ctx.waitForEvent('page', { timeout: 3000 }).catch(() => null);
await say('What is 2+2?');
if (await nothing) fail('an ordinary message opened a tab');
else ok('ordinary messages open nothing');


// --- the fault that made this look broken three times ---------------------
//
// window.open() on iOS Safari, called from a fetch callback, can return
// a truthy Window that never navigates. Every way of detecting a blocked
// popup says it worked, and the page leaves nothing behind. Chromium
// cannot reproduce it -- it genuinely opens the tab -- so the check is
// that a link is ALWAYS left, whether the tab opened or not.
{
  const before = await page.$$eval('.msg.jarvis', (n) => n.length);
  const opened = ctx.waitForEvent('page', { timeout: 8000 }).catch(() => null);
  await say('Open LinkedIn');
  const tab = await opened;
  if (tab) await tab.close();

  const link = await page.$('.msg.jarvis:last-child a.btn');
  if (!link)
    fail('the tab opened and no link was left — on iOS that is a dead end, ' +
         'because a popup can be suppressed while reporting success');
  else {
    const href = await link.getAttribute('href');
    const target = await link.getAttribute('target');
    if (!href || !href.includes('linkedin.com'))
      fail(`the link points at ${href}`);
    else if (target !== '_blank') fail('the link does not open a new tab');
    else ok('a real link is left behind even when the tab did open');
  }
}

// --- naming a browser -----------------------------------------------------
{
  const opened = ctx.waitForEvent('page', { timeout: 5000 }).catch(() => null);
  await say('open linkedin in Chrome');
  const tab = await opened;
  if (tab) await tab.close();
  const href = await page.getAttribute('.msg.jarvis:last-child a.btn', 'href')
    .catch(() => null);
  // This runs with a desktop user agent, where no page can choose the
  // browser -- the operating system does. So the address must stay
  // https rather than becoming a scheme that silently does nothing.
  // The iPad case, where googlechromes:// is real, is in spoken_open.mjs.
  if (!href || !href.startsWith('https://'))
    fail(`on a desktop, "in Chrome" produced ${href} — a scheme that ` +
         `cannot work here is worse than the plain address`);
  else if (!href.includes('linkedin.com'))
    fail(`the link points at ${href}`);
  else ok('"in Chrome" on a desktop stays an https link, because the ' +
          'computer chooses the browser, not the page');
}

// --- playing something, and the things a link cannot do -------------------
//
// "Play X on Spotify" is the request this was actually built for. The
// address is a universal link, so on an iPad it hands off to the Spotify
// app and on a laptop it opens the web player -- one address, both
// outcomes, nothing to install.
await ctx.route('**://open.spotify.com/**', (route) =>
  route.fulfill({ status: 200, contentType: 'text/html', body: '<h1>stub</h1>' }));

const playing = ctx.waitForEvent('page', { timeout: 10000 }).catch(() => null);
await say('Play Blinding Lights on Spotify');
const three = await playing;
if (!three) fail('"Play X on Spotify" opened nothing');
else if (!three.url().includes('open.spotify.com/search/Blinding+Lights'))
  fail(`it went to ${three.url()}`);
else ok('"Play Blinding Lights on Spotify" opens Spotify on that search');
if (three) await three.close();

// --- a timer is not a web address ----------------------------------------
//
// Without the shortcut, the page must say so and offer the way to fix
// it, rather than silently doing nothing -- which is what every other
// "nothing happened" in this project turned out to be.
await say('set a timer for 10 minutes');
const offered = await page.textContent('.msg.jarvis:last-child').catch(() => '');
if (!/cannot set a timer/i.test(offered || ''))
  fail(`a timer request said: ${(offered || '').slice(0, 120)}`);
else if (!/shortcut/i.test(offered || ''))
  fail('it does not mention the shortcut, so there is nothing to act on');
else ok('a timer says plainly that JARVIS has no timer, and what to do');

const guide = await page.getAttribute('.msg.jarvis:last-child a', 'href')
  .catch(() => null);
if (guide !== '/shortcut.html') fail(`the setup link points at ${guide}`);
else {
  const res = await page.request.get(BASE + guide);
  if (!res.ok()) fail(`the setup guide is not served (HTTP ${res.status()})`);
  else ok('and links to a setup guide the device can actually reach');
}

// --- once it is there, the link is built correctly ------------------------
const built = await page.evaluate(() => {
  localStorage.setItem('jarvis.shortcut', 'yes');
  const back = encodeURIComponent(location.href);
  return 'shortcuts://x-callback-url/run-shortcut?name=JARVIS&input=text' +
         '&text=' + encodeURIComponent('set a timer for 10 minutes') +
         '&x-success=' + back;
});
if (!built.startsWith('shortcuts://x-callback-url/run-shortcut?name=JARVIS'))
  fail(`the shortcut link is malformed: ${built}`);
else if (!built.includes('x-success='))
  fail('nothing brings him back to JARVIS afterwards');
else ok('the shortcut link names JARVIS and carries a way back');

if (errors.length) fail('the page logged errors: ' + errors.join(' | '));
console.log(out.map((l) => '  ' + l).join('\n'));
await browser.close();
