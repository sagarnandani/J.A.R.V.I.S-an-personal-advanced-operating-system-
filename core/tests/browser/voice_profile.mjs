/* The spoken reply path.
 *
 * Chromium in a sandbox has no audio device and no system voices, so this
 * cannot check what JARVIS sounds like. What it can check is everything
 * that made the old version wrong: that the page asks the server for the
 * voice instead of deciding for itself, that a long reply is queued as
 * sentences rather than truncated at 800 characters, that turning voice
 * off stops the whole queue and not just the sentence in flight, and that
 * the utterances handed to the browser carry the profile's settings.
 *
 * It works by replacing window.speechSynthesis with a recorder before the
 * page runs. Everything above that boundary is the real code.
 */
import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:8099';
const out = [];
let failed = false;
const fail = (m) => { console.error('FAIL: ' + m); process.exitCode = 1; failed = true; };
const ok = (m) => { if (!failed) out.push(m); failed = false; };

const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
const ctx = await browser.newContext({ viewport: { width: 1180, height: 820 } });

// Stand in for the device's synthesiser, and record everything it is asked
// to say. Installed before any page script runs.
await ctx.addInitScript(() => {
  const spoken = [];
  window.__spoken = spoken;
  const voicesList = [
    { name: 'Daniel', lang: 'en-GB', default: false },
    { name: 'Samantha', lang: 'en-US', default: true },
    { name: 'Lekha', lang: 'hi-IN', default: false },
  ];
  // window.speechSynthesis is a getter on Window.prototype with no setter,
  // so a plain assignment does nothing and the real (absent) engine stays.
  const stub = {
    getVoices: () => voicesList,
    addEventListener() {},
    cancel() { window.__cancels = (window.__cancels || 0) + 1; },
    speak(u) {
      spoken.push({ text: u.text, rate: u.rate, pitch: u.pitch,
                    volume: u.volume, lang: u.lang,
                    voice: u.voice ? u.voice.name : null });
      // Real utterances fire these; the queue depends on onend.
      setTimeout(() => u.onstart && u.onstart(), 1);
      setTimeout(() => u.onend && u.onend(), 12);
    },
  };
  Object.defineProperty(window, 'speechSynthesis',
                        { value: stub, configurable: true });
  window.SpeechSynthesisUtterance = function (text) {
    this.text = text; this.rate = 1; this.pitch = 1; this.volume = 1;
    this.lang = ''; this.voice = null;
  };
});

const page = await ctx.newPage();
const errors = [];
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', (e) => errors.push(String(e)));

await page.goto(BASE, { waitUntil: 'networkidle' });
await page.waitForSelector('#app:not(.hidden)', { timeout: 15000 });
ok('signed in (dev mode), dashboard visible');

// --- the profile comes from the server ------------------------------------
const served = await (await fetch(BASE + '/v1/voice')).json();
if (served.profile.name !== 'JARVIS_VOICE_V1') fail('the served profile is not JARVIS_VOICE_V1');
if (served.engine.name !== 'browser') fail(`unexpected engine: ${served.engine.name}`);
if (served.live.takes_profile !== false)
  fail('the live path claims it can take the voice profile');
ok(`server serves ${served.profile.name} for the ${served.engine.name} engine`);

if (Object.keys(served.deliveries).length !== 6)
  fail(`expected six deliveries, got ${Object.keys(served.deliveries).length}`);
ok(`six deliveries offered: ${Object.keys(served.deliveries).join(', ')}`);

// --- turning voice on speaks, using the profile ---------------------------
await page.click('#speakToggle');
await page.waitForFunction(() => (window.__spoken || []).length > 1, null, { timeout: 10000 });

const first = await page.evaluate(() => window.__spoken.filter((s) => s.text.trim()));
if (!first.length) fail('nothing was spoken when voice was switched on');
else {
  const u = first[0];
  if (u.voice !== 'Daniel') fail(`picked ${u.voice} rather than the preferred British male`);
  if (Math.abs(u.rate - served.deliveries.success.settings.rate) > 0.001)
    fail(`rate ${u.rate} is not the profile's ${served.deliveries.success.settings.rate}`);
  if (Math.abs(u.pitch - served.deliveries.success.settings.pitch) > 0.001)
    fail(`pitch ${u.pitch} is not the profile's`);
  ok(`spoke as ${u.voice} at rate ${u.rate}, pitch ${u.pitch}, from the served profile`);
}

// --- a long reply is queued as sentences, and nothing is dropped ----------
await page.evaluate(() => { window.__spoken.length = 0; });
const long = "Good evening, Sagar. All primary systems are operational. " +
  "You have three unfinished priorities today, and I recommend completing " +
  "the first one before we proceed. The analysis is complete. " +
  "Your meeting begins in twenty minutes. At your current pace, arriving " +
  "on time would be an unexpected achievement, sir.";
await page.evaluate((t) => window.jarvisSpeak(t), long);
await page.waitForFunction(() => (window.__spoken || []).length >= 3, null, { timeout: 10000 });
await page.waitForTimeout(400);

const lines = await page.evaluate(() => window.__spoken.map((s) => s.text));
if (lines.length < 4) fail(`a six-sentence reply became ${lines.length} utterance(s)`);
const rebuilt = lines.join(' ').replace(/\s+/g, ' ').trim();
if (rebuilt !== long.replace(/\s+/g, ' ').trim())
  fail(`words were lost or reordered:\n  got: ${rebuilt}`);
if (lines.some((l) => l.length > 400)) fail('a line was not split');
ok(`${lines.length} sentences queued, every word intact, nothing truncated`);

// --- turning it off stops the queue, not just the current sentence --------
await page.evaluate(() => { window.__spoken.length = 0; });
await page.evaluate((t) => window.jarvisSpeak(t), long);
await page.waitForFunction(() => (window.__spoken || []).length >= 1, null, { timeout: 5000 });
await page.click('#speakToggle');           // off
const atStop = await page.evaluate(() => window.__spoken.length);
await page.waitForTimeout(600);
const after = await page.evaluate(() => window.__spoken.length);
if (after > atStop) fail(`the queue kept speaking after voice was turned off (${atStop} then ${after})`);
ok('turning voice off empties the queue, it does not carry on');

// --- a reply in another script uses a voice that can read it -------------
await page.click('#speakToggle');           // back on
await page.waitForTimeout(200);
await page.evaluate(() => { window.__spoken.length = 0; });
await page.evaluate(() => window.jarvisSpeak('नमस्ते सागर, सब कुछ ठीक है।'));
await page.waitForTimeout(400);
const hindi = await page.evaluate(() => window.__spoken[0]);
if (!hindi) fail('a Hindi reply was not spoken at all');
else if (hindi.voice !== 'Lekha') fail(`read Hindi with ${hindi.voice}`);
else ok('a Hindi reply is read by the Hindi voice, not the British one');

await page.screenshot({ path: process.argv[2] || 'voice.png' });
const real = errors.filter((e) => !/Failed to load resource/.test(e));
if (real.length) fail('console errors: ' + real.join(' | '));

console.log(out.map((l) => '  ok  ' + l).join('\n'));
await browser.close();
