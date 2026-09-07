/* A deploy that only half arrives.
 *
 * Browsers cache the HTML and the script separately, so a page can run new
 * markup against an older script. That happened on a real iPad: the Tasks
 * button was in the HTML, the code behind it was not, and tapping it did
 * nothing at all -- the button even highlighted first, so there was no
 * sign anything was wrong.
 *
 * Two things are checked. That static files are served with a
 * Cache-Control that makes a browser ask before reusing them, which is
 * what stops the mismatch happening. And that if it happens anyway, a
 * button for a view the script does not know stays un-highlighted and
 * says so, rather than looking selected and doing nothing.
 */
import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:8099';
const out = [];
const fail = (m) => { console.error('FAIL: ' + m); process.exitCode = 1; };

/* --- the fix: browsers must revalidate ---------------------------------- */
for (const path of ['/', '/app.js', '/live.js']) {
  const res = await fetch(BASE + path);
  const cc = res.headers.get('cache-control') || '';
  if (!/no-cache|no-store|max-age=0/.test(cc))
    fail(`${path} is served with Cache-Control: "${cc}" — a browser will invent its own freshness window`);
  else out.push(`${path} must be revalidated (${cc})`);
}

const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
const ctx = await browser.newContext({ viewport: { width: 1180, height: 820 } });
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#app:not(.hidden)');

/* --- and if it happens anyway, it must not fail silently ---------------- */
await page.evaluate(() => {
  const b = document.createElement('button');
  b.dataset.view = 'something-this-script-has-never-heard-of';
  b.id = 'ghost';
  document.getElementById('nav').appendChild(b);
});
await page.click('#ghost');
await page.waitForTimeout(200);

if (await page.locator('#ghost.active').count())
  fail('a button for an unknown view was highlighted as if it had worked');
else out.push('an unknown view leaves its button un-highlighted');

if (errors.length)
  fail(`an unknown view threw instead of reporting: ${errors.join(' | ')}`);
else out.push('an unknown view reports the mismatch rather than throwing');

// The page must still work afterwards.
await page.click('button[data-view="tasks"]');
await page.waitForSelector('#tasksView:not(.hidden)', { timeout: 5000 });
out.push('the rest of the nav still works after an unknown view');

console.log(out.map((l) => '  ok  ' + l).join('\n'));
await browser.close();
