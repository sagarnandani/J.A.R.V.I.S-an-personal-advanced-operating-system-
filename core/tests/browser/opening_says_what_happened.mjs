/* What JARVIS says when it opens something, on the device it is used on.
 *
 * "Jarvis still unable to take control of YouTube. It opens but nothing
 * happens next. It also not opening LinkedIn, Instagram etc."
 *
 * The addresses were always right. Two things were wrong:
 *
 * On iOS, Safari blocks window.open outside a tap -- always. So the tab
 * never opened by itself, JARVIS said "Opening LinkedIn.", nothing
 * opened, and the link that WAS the mechanism sat underneath labelled
 * as a fallback for an unlikely case. That reads as a broken assistant,
 * and he read it that way three times.
 *
 * And "play X" resolved to a search results page. A correct URL and a
 * wrong answer: he said play, the site opened, nothing played.
 *
 * Run at iPad size with an iPad user agent, because the bug only exists
 * on the device it was reported from.
 */
import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:8099';
const IPAD_UA = 'Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 '
  + '(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1';
const out = [];
let failed = false;
const fail = (m) => { console.error('FAIL: ' + m); process.exitCode = 1; failed = true; };
const ok = (m) => { if (!failed) out.push(m); failed = false; };

const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });

async function ask(page, text) {
  // Counted BEFORE the click. Reading it afterwards is a race: on a warm
  // server the reply lands first, the count already includes the new
  // link, and the wait sits there for a second one that never comes.
  const before = await page.evaluate(
    () => document.querySelectorAll('.convo a.btn').length);
  await page.fill('#msg', text);
  await page.click('#sendBtn');
  await page.waitForFunction(
    (n) => document.querySelectorAll('.convo a.btn').length > n,
    before, { timeout: 15000 });
  return await page.evaluate(() => {
    const links = [...document.querySelectorAll('.convo a.btn')];
    const link = links[links.length - 1];
    const msg = link.closest('.m') || link.parentElement.parentElement;
    return { href: link.href, label: (link.textContent || '').trim(),
             said: (msg.querySelector('.body') || {}).textContent || '',
             note: (msg.querySelector('.note') || {}).textContent || '' };
  });
}

// --- on an iPad -----------------------------------------------------------
{
  const ctx = await browser.newContext({
    viewport: { width: 820, height: 1180 }, userAgent: IPAD_UA,
    hasTouch: true, isMobile: false });
  const page = await ctx.newPage();
  await page.addInitScript(() => { window.open = () => null; });
  await page.goto(BASE, { waitUntil: 'networkidle' });
  await page.waitForSelector('#app:not(.hidden)', { timeout: 15000 });

  for (const site of ['LinkedIn', 'Instagram', 'YouTube']) {
    const got = await ask(page, `open ${site}`);
    if (!got.href.includes(site.toLowerCase()))
      fail(`"open ${site}" produced ${got.href}`);
    // The whole complaint: it said it was opening and nothing opened.
    if (/^Opening /.test(got.said))
      fail(`${site}: still claims "Opening" on an iPad, where nothing can open`);
    if (!/tap/i.test(got.label))
      fail(`${site}: the link says "${got.label}", not that he has to tap it`);
    if (!/tap/i.test(got.note))
      fail(`${site}: the note does not say Safari is waiting for a tap`);
    ok(`open ${site}: "${got.said.trim().slice(0, 40)}" → ${got.label}`);
  }
}

// --- on a laptop, where it really can open --------------------------------
{
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  await page.addInitScript(() => { window.__opened = []; window.open = (u) => { window.__opened.push(u); return {}; }; });
  await page.goto(BASE, { waitUntil: 'networkidle' });
  await page.waitForSelector('#app:not(.hidden)', { timeout: 15000 });

  const got = await ask(page, 'open LinkedIn');
  const opened = await page.evaluate(() => window.__opened);
  if (!opened.length) fail('on a laptop it did not even try to open a tab');
  if (/tap to open/i.test(got.label))
    fail('on a laptop it tells him to tap, when the tab really did open');
  ok(`laptop: tried window.open(${opened[0]}) and says "${got.label}"`);
}

console.log(out.map((l) => '  ok  ' + l).join('\n'));
await browser.close();
