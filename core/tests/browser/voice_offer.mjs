/* An offer that arrived from a spoken turn.
 *
 * The websocket frame is dispatched directly rather than driving a real
 * microphone: what is being checked is that a voice offer renders as the
 * same card as a typed one and is just as actionable. Everything after
 * the button -- planner, agents, workflow -- is the real thing.
 */
import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:8099';
const out = [];
let failed = false;
const fail = (m) => { console.error('FAIL: ' + m); process.exitCode = 1; failed = true; };
// ok() rather than ok(): an earlier version pushed its success
// line whether or not the assertion above it held, so a run could
// print FAIL and then claim the same step had worked.
const ok = (m) => { if (!failed) out.push(m); failed = false; };

const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
const ctx = await browser.newContext({ viewport: { width: 1180, height: 820 } });
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));

// The check server runs with no Gemini key, so research.web cannot run --
// and since the fallback now correctly reaches for it, every run would end
// in an honest "no API key" failure. These checks are about the panel and
// the card, not about which agent gets picked, so the workflow is given an
// explicit step that works keyless. Everything else -- the real workflow,
// the real polling, the real rendering -- is untouched.
await page.route('**/v1/workflows', async (route) => {
  if (route.request().method() !== 'POST') return route.continue();
  const body = route.request().postDataJSON() || {};
  if (!body.steps || !body.steps.length) {
    body.steps = [{ capability: 'general.analysis', objective: body.objective,
                    name: 'step1', after: [] }];
  }
  await route.continue({ postData: JSON.stringify(body) });
});

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#app:not(.hidden)');

// Exactly the frame app/routes/live.py sends on a spoken request.
await page.evaluate(() => document.dispatchEvent(new CustomEvent('jarvis:offer', {
  detail: {
    type: 'offer',
    objective: 'find the current Karnataka EV subsidy and check it',
    cost_note: 'about Rs.1.40, going by what runs like this have cost',
    typical_cost_inr: 1.4,
  },
})));

await page.waitForSelector('.offer', { timeout: 5000 });
const text = await page.locator('.offer').first().textContent();
if (!/Karnataka EV subsidy/.test(text)) fail('the voice offer does not say what it would do');
if (!/Rs\.1\.40/.test(text)) fail('the voice offer does not say what it would cost');
ok('a spoken request renders the same card as a typed one');

const buttons = await page.locator('.offer button').count();
if (buttons !== 2) fail(`expected Go ahead / No thanks, saw ${buttons} button(s)`);
ok('it offers the same two choices');

// And it actually runs, through the real planner and agents.
await page.locator('.offer button', { hasText: 'Go ahead' }).click();
await page.waitForSelector('.offer.done, .offer.failed', { timeout: 90000 });
if (await page.locator('.offer.failed').count())
  fail(`the run failed: ${await page.locator('.offer').first().textContent()}`);
ok('accepting a spoken offer runs it, same as a typed one');

const result = await page.locator('.offer.done').first().textContent();
if (!/Rs\./.test(result)) fail('the result does not say what it cost');
ok('the answer lands in the conversation with its cost');

if (errors.length) fail('console errors: ' + errors.join(' | '));
console.log(out.map((l) => '  ok  ' + l).join('\n'));
await browser.close();
