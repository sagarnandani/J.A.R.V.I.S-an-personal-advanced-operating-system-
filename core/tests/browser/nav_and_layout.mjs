/* Finding your way around.
 *
 * The Tasks tab was on screen for days and the owner could not see it:
 * four unlabelled icons in a corner is not navigation. So the buttons
 * carry their names wherever there is room, and this checks they are
 * readable, on screen, and do not push the page sideways -- at the three
 * sizes JARVIS is actually used at.
 *
 * It also checks the Activity panel stays gone. A list of "Message
 * answered - success" rows told the owner nothing they had not just
 * watched happen.
 */
import { chromium } from 'playwright';
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
for (const [w, h, label, shot] of [[1180, 820, 'iPad landscape', process.argv[2]],
                                   [820, 1180, 'iPad portrait', null],
                                   [390, 750, 'iPhone', process.argv[3]]]) {
  const page = await (await browser.newContext({ viewport: { width: w, height: h } })).newPage();
  await page.goto('http://127.0.0.1:8099', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#app:not(.hidden)');
  const r = await page.evaluate(() => {
    const b = document.querySelector('button[data-view="tasks"]');
    const rect = b.getBoundingClientRect();
    const span = b.querySelector('span');
    return { text: (b.textContent || '').trim(),
             labelShown: span ? getComputedStyle(span).display !== 'none' : false,
             onScreen: rect.right <= window.innerWidth + 1 && rect.bottom <= window.innerHeight + 1,
             scrollW: document.documentElement.scrollWidth, w: window.innerWidth,
             activityGone: !document.getElementById('feed') };
  });
  const bad = [];
  if (!r.onScreen) bad.push('the Tasks button is off screen');
  if (r.scrollW > r.w + 1) bad.push(`sideways scroll ${r.scrollW}>${r.w}`);
  if (!r.activityGone) bad.push('the Activity panel is still there');
  if (w >= 900 && (!r.labelShown || r.text !== 'Tasks'))
    bad.push(`no readable label (text=${JSON.stringify(r.text)}, shown=${r.labelShown})`);
  console.log(bad.length ? `FAIL ${label}: ${bad.join('; ')}`
                         : `  ok  ${label} (${w}×${h}): Tasks${r.labelShown ? ' labelled' : ' as an icon'}, on screen, no Activity panel`);
  if (bad.length) process.exitCode = 1;
  if (shot) await page.screenshot({ path: shot });
}
await browser.close();
