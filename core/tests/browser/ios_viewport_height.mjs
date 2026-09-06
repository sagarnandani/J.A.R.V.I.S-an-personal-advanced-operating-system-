/* Modelling what iOS Safari does, which Chromium does not do by itself.
 *
 * On iOS, 100vh is the LARGE viewport: the height the page would have if
 * the toolbars were hidden. The visible area is smaller by the address bar
 * and tab bar. So a block sized calc(100vh - x) is laid out against the
 * big number and displayed inside the small one, and its bottom edge is
 * unreachable.
 *
 * Chromium's 100vh equals what you can see, so it can never show this by
 * itself. The trick is to render at the height iOS REPORTS and assert
 * everything fits inside the height iOS actually SHOWS.
 */
import { chromium } from 'playwright';

const REPORTED = 820;   // what iOS calls 100vh on an iPad in landscape
const VISIBLE  = 690;   // what is left once Safari's chrome is on screen

const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
const ctx = await browser.newContext({ viewport: { width: 1180, height: REPORTED } });
const page = await ctx.newPage();
await page.goto('http://127.0.0.1:8099', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#app:not(.hidden)');
await page.click('button[data-view="tasks"]');
await page.waitForSelector('#tasksView:not(.hidden)');
await page.waitForSelector('#wfList .wf');
await page.click('#wfList .wf:has-text("Karnataka EV subsidy, checked")');
await page.waitForSelector('#planSteps .step');

const bottom = await page.evaluate(() =>
  Math.round(document.getElementById('planSteps').getBoundingClientRect().bottom));

if (bottom > VISIBLE) {
  console.error(`FAIL  results end at ${bottom}px, but an iPad only shows ${VISIBLE}px ` +
                `(sized against the ${REPORTED}px iOS reports) — ${bottom - VISIBLE}px unreachable`);
  process.exitCode = 1;
} else {
  console.log(`  ok  results end at ${bottom}px, inside the ${VISIBLE}px an iPad really shows`);
}
await browser.close();
