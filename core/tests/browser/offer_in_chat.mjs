/* Asking JARVIS for something in the chat box and having it done.
 *
 * The mock provider cannot decide to mark a message, so the marked reply
 * is injected at the network boundary -- everything downstream of that is
 * the real thing: the real strip, the real registry check, the real
 * planner, the real agents, the real workflow.
 */
import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:8099';
const out = [];
const fail = (m) => { console.error('FAIL: ' + m); process.exitCode = 1; };

const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
const ctx = await browser.newContext({ viewport: { width: 1180, height: 820 } });
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));

// Stand in for a model that decided this one needs looking up.
await page.route('**/v1/message', async (route) => {
  const res = await route.fetch();
  const body = await res.json();
  body.reply = 'My own figure is likely out of date, sir.';
  body.offer = {
    objective: 'find the current EV subsidy in Karnataka and check it',
    cost_note: 'this would be the first one, so I have no measurement yet',
    typical_cost_inr: null,
  };
  await route.fulfill({ response: res, json: body });
});

await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#app:not(.hidden)');

await page.fill('#msg', 'What is the EV subsidy in Karnataka now?');
await page.click('#sendBtn');
await page.waitForSelector('.offer', { timeout: 30000 });
out.push('an offer appears under the reply');

const offerText = await page.locator('.offer').first().textContent();
if (!/EV subsidy in Karnataka/.test(offerText)) fail('the offer does not say what it would do');
if (!/no measurement yet/.test(offerText)) fail('the offer does not say what it would cost');
if (/JARVIS_CAN_DO/.test(await page.locator('.msg.jarvis .body').last().textContent()))
  fail('the marker leaked into the reply the owner reads');
out.push('it says what it would do, what it would cost, and shows no machinery');

// Declining must run nothing.
const before = (await (await fetch(BASE + '/v1/workflows?limit=50')).json()).length;
await page.locator('.offer button', { hasText: 'No thanks' }).click();
await page.waitForTimeout(300);
if ((await (await fetch(BASE + '/v1/workflows?limit=50')).json()).length !== before)
  fail('declining an offer still started work');
if (!/Nothing was run/.test(await page.locator('.offer').first().textContent()))
  fail('declining did not say so');
out.push('declining runs nothing and says so');

// Accepting runs it.
await page.fill('#msg', 'And check it properly');
await page.click('#sendBtn');
await page.waitForSelector('.offer:not(.done):not(.failed) button', { timeout: 30000 });
await page.locator('.offer button', { hasText: 'Go ahead' }).last().click();

await page.waitForSelector('.offer.done, .offer.failed', { timeout: 90000 });
const box = page.locator('.offer.done, .offer.failed').last();
if (await page.locator('.offer.failed').count()) fail(`the run failed: ${await box.textContent()}`);
out.push('accepting plans and runs it, without leaving the conversation');

const result = await box.textContent();
if (!/Rs\./.test(result)) fail('the result does not say what it cost');
if (!/see the steps/.test(result)) fail('there is no way through to the full trace');
out.push(`the answer comes back in the chat with its cost: "${result.trim().slice(-40)}"`);

// It must be remembered, or tomorrow JARVIS will not know it.
const kept = await (await fetch(BASE + '/v1/memories?limit=100')).json();
if (!kept.some((m) => /Looked into:/.test(m.content || '')))
  fail('what the work found was not remembered');
out.push('what it found is stored in memory');

// And the link into the Tasks tab works.
await box.locator('[data-open-tasks]').click();
await page.waitForSelector('#tasksView:not(.hidden)', { timeout: 5000 });
await page.waitForSelector('#planSteps .step', { timeout: 10000 });
out.push('"see the steps" opens the run in the Tasks tab');

if (errors.length) fail('console errors: ' + errors.join(' | '));
console.log(out.map((l) => '  ok  ' + l).join('\n'));
await browser.close();
