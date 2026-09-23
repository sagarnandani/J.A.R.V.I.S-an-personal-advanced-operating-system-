import { startLive, stopLive, liveActive, voiceCheck } from './live.js';

/* JARVIS dashboard.
 *
 * The one rule: nothing on screen is invented. Every figure comes from
 * GET /v1/dashboard, which measures it. Where JARVIS cannot yet know
 * something, the panel says so rather than showing a plausible number --
 * a dashboard you cannot trust is worse than no dashboard.
 */
const $ = (id) => document.getElementById(id);
const gate = $("gate"), app = $("app");

// Sign-in lives in a cookie the server sets, so nothing about it is kept
// here -- page memory dies on reload, which is what used to make JARVIS
// appear to forget a sign-in that had actually worked.
// Every call to JARVIS goes through here, so this is the one place that
// can notice the back end has stopped answering.
//
// It used to be a bare fetch. Callers checked `res.ok`, returned quietly
// when it was false, and the page carried on looking healthy -- so a
// signed-out session, an older image without a route, and a server that
// is simply not running all looked identical from the outside: a full
// dashboard whose buttons do nothing.
//
// The Response is still returned unchanged and network errors still
// reject, so every existing caller behaves exactly as before. The only
// addition is that the failure becomes visible.
const api = async (path, opts = {}) => {
  try {
    const res = await fetch(path, {
      ...opts,
      credentials: "same-origin",
      headers: { ...(opts.headers || {}), "Content-Type": "application/json" },
    });
    if (res.ok) clearTrouble(); else showTrouble(path, res.status);
    return res;
  } catch (err) {
    // Never reached the server at all: down, wrong port, DNS, or the
    // browser blocked it.
    showTrouble(path, 0);
    throw err;
  }
};

// What each failure actually means, in the words of the thing to do next.
// Written for someone reading it on a phone with no terminal open.
function troubleText(path, status) {
  if (status === 0)
    return "JARVIS is not answering at all. The server may be stopped — " +
           "on the machine running it: docker compose ps, then " +
           "docker compose logs --tail=50 jarvis";
  if (status === 401 || status === 403)
    return "You are signed out, so nothing on this page can reach JARVIS. " +
           "Reload and sign in again. If signing in does not stick, the " +
           "browser is not keeping the login cookie — over plain http that " +
           "means COOKIE_SECURE must be false.";
  if (status === 404)
    return `This deployment has no ${path} — it is probably running an ` +
           "older build than the page you are looking at. On the server: " +
           "git pull && docker compose up -d --build";
  if (status === 503)
    return "JARVIS refused the request: usually no model provider is " +
           "configured, or a provider you asked for by name is not set up.";
  if (status >= 500)
    return `JARVIS hit an error on ${path} (${status}). The reason is in ` +
           "the log: docker compose logs --tail=50 jarvis";
  return `JARVIS refused ${path} (${status}).`;
}

let troubleShown = "";

function showTrouble(path, status) {
  const banner = $("trouble");
  if (!banner) return;
  // Before sign-in every call is a 401 by design, and the sign-in panel
  // is already on screen saying so. A banner there would be noise on the
  // one screen that is working correctly.
  if ((status === 401 || status === 403) &&
      gate && !gate.classList.contains("hidden")) return;
  // Keyed, so a page polling four endpoints does not stack four banners
  // or flicker between them.
  const key = `${status}`;
  if (troubleShown === key) return;
  troubleShown = key;
  $("troubleText").textContent = troubleText(path, status);
  banner.hidden = false;
}

function clearTrouble() {
  const banner = $("trouble");
  if (!banner || banner.hidden) return;
  banner.hidden = true;
  troubleShown = "";
}

if ($("troubleClose")) {
  $("troubleClose").onclick = () => { $("trouble").hidden = true; };
}

const esc = (s) => { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; };
const pad = (n) => String(n).padStart(2, "0");

/* ---------------------------------------------------------------- clock */
function tick() {
  const d = new Date();
  let h = d.getHours();
  const ampm = h >= 12 ? "PM" : "AM";
  h = h % 12 || 12;
  $("clock").innerHTML = `${pad(h)}:${pad(d.getMinutes())} <small>${ampm} ${pad(d.getSeconds())}</small>`;
  $("dateBig").textContent = d.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" }).toUpperCase();
  $("dayName").textContent = d.toLocaleDateString(undefined, { weekday: "long" }).toUpperCase();
}
setInterval(tick, 1000); tick();

/* ------------------------------------------------------------- waveform */
// 22 bars that move only while JARVIS is actually busy. A waveform that
// dances at rest is decoration pretending to be information.
const wave = $("wave");
for (let i = 0; i < 22; i++) wave.appendChild(document.createElement("i"));
const bars = [...wave.children];
let waveTimer = null;
function setBusy(on) {
  $("reactor").classList.toggle("busy", on);
  wave.classList.toggle("active", on);
  clearInterval(waveTimer);
  if (on) {
    waveTimer = setInterval(() => {
      bars.forEach((b, i) => {
        b.style.height = 4 + Math.abs(Math.sin(Date.now() / 190 + i * 0.7)) * 20 + "px";
      });
    }, 70);
  } else {
    bars.forEach((b) => (b.style.height = "4px"));
  }
}
setBusy(false);

/* ----------------------------------------------------------------- voice */
/* Safari's built-in speech engine: no API, no key, no cost, no quota.
 *
 * Two things make it fail silently, and both bit us:
 *
 * 1. iOS refuses to speak unless the speech was started by a real tap.
 *    A reply arrives after a network round trip, which is not a tap, so
 *    every single reply was blocked with no error anywhere. The fix is to
 *    unlock the engine during a tap we DO have -- the Send button -- by
 *    speaking one silent utterance. After that the page may speak freely.
 *
 * 2. getVoices() is empty until the browser has loaded them, which
 *    happens asynchronously and often after the first render. Asking once
 *    at start-up and caching the empty answer means no voice is ever
 *    chosen.
 */
let speakOn = false;
try { speakOn = localStorage.getItem("jarvis.speak") === "1"; } catch (e) {}
const canSpeak = "speechSynthesis" in window;
let speechUnlocked = false;
let voices = [];

// Must be called from inside a real user gesture. Speaking one silent
// utterance is what actually lifts iOS's restriction; nothing else does.
function unlockSpeech() {
  if (!canSpeak || speechUnlocked) return;
  try {
    const u = new SpeechSynthesisUtterance(" ");
    u.volume = 0;
    speechSynthesis.speak(u);
    speechUnlocked = true;
  } catch (e) { /* nothing useful to say; speak() will report if it fails */ }
}

function loadVoices() { if (canSpeak) voices = speechSynthesis.getVoices() || []; }
if (canSpeak) {
  loadVoices();
  speechSynthesis.addEventListener("voiceschanged", loadVoices);
}

/* JARVIS_VOICE_V1, fetched rather than decided here.
 *
 * This page used to hold its own opinion about the voice: a regular
 * expression matching acceptable names, a rate and a pitch a few lines
 * below it, and no way to change any of it without a deploy. All of that
 * now lives in app/voice on the server, so there is one answer to "what
 * does JARVIS sound like" and the page simply asks for it. */
let VOICE = null;

async function loadVoiceProfile() {
  if (VOICE) return VOICE;
  try {
    const res = await api("/v1/voice");
    if (res.ok) VOICE = await res.json();
  } catch (e) { /* the defaults below still speak */ }
  return VOICE;
}

// If the profile cannot be fetched, JARVIS still talks. Silence because a
// settings request failed would be the worst of both.
const FALLBACK_SETTINGS = { rate: 0.85, pitch: 0.92, volume: 1,
                            lang: "en-GB", prefer: ["Arthur", "Daniel"] };

function settingsFor(delivery) {
  if (!VOICE) return FALLBACK_SETTINGS;
  const mode = VOICE.deliveries && VOICE.deliveries[delivery];
  return (mode && mode.settings) || VOICE.settings || FALLBACK_SETTINGS;
}

/* How fast this device actually speaks.
 *
 * A rate of 1.0 means different words per minute on every voice and every
 * platform, so the server can only estimate it. The page can measure it:
 * it knows the words it sent and can time how long they took. After the
 * first real utterance the estimate is replaced by an observation, and it
 * is kept per device because it is a property of the device. */
const CAL_KEY = "jarvis.voice.wpm";
let observedWpm = null;
try {
  const stored = parseFloat(localStorage.getItem(CAL_KEY));
  if (stored > 40 && stored < 400) observedWpm = stored;
} catch (e) {}

function calibrate(words, seconds) {
  if (!words || seconds < 0.6) return;   // too short to measure anything
  const wpm = (words / seconds) * 60;
  if (wpm < 40 || wpm > 400) return;     // implausible; the tab was hidden
  // Averaged rather than replaced, so one odd measurement does not move it.
  observedWpm = observedWpm ? observedWpm * 0.7 + wpm * 0.3 : wpm;
  try { localStorage.setItem(CAL_KEY, String(Math.round(observedWpm))); } catch (e) {}
}

function correctedRate(rate) {
  const target = VOICE && VOICE.profile && VOICE.profile.words_per_minute;
  if (!observedWpm || !target) return rate;
  // observedWpm was produced at some rate; scale toward the target and
  // clamp, because a runaway correction is worse than a slightly wrong pace.
  const factor = target / observedWpm;
  return Math.max(0.5, Math.min(2, rate * Math.max(0.6, Math.min(1.6, factor))));
}

// Which language the reply is in, read from the script it is written in.
// JARVIS answers in whatever language you use, so reading it back in an
// English voice would be unintelligible -- Devanagari spoken by an
// English voice is noise.
const SCRIPTS = [
  [/[\u0C80-\u0CFF]/, "kn"],   // Kannada
  [/[\u0900-\u097F]/, "hi"],   // Devanagari: Hindi, Marathi
  [/[\u0B80-\u0BFF]/, "ta"],   // Tamil
  [/[\u0C00-\u0C7F]/, "te"],   // Telugu
  [/[\u0980-\u09FF]/, "bn"],   // Bengali
  [/[\u0A80-\u0AFF]/, "gu"],   // Gujarati
  [/[\u0600-\u06FF]/, "ar"],   // Arabic
  [/[\u4E00-\u9FFF]/, "zh"],
  [/[\u3040-\u30FF]/, "ja"],
  [/[\uAC00-\uD7AF]/, "ko"],
];
function langOf(text) {
  for (const [re, code] of SCRIPTS) if (re.test(text)) return code;
  return null;
}

function pickVoice(lang, prefer) {
  if (!voices.length) return null;
  if (lang) {
    // An Indian-English voice reads Hindi transliteration far better than
    // a British one, so a regional fallback is worth having.
    return (
      voices.find((v) => v.lang.toLowerCase().startsWith(lang)) ||
      voices.find((v) => /en-IN/i.test(v.lang)) ||
      null
    );
  }
  // Named voices first, in the order the profile lists them, then any
  // British male, then any British voice at all. The names come from the
  // server so a better voice appearing on a device is a config change.
  for (const name of prefer || []) {
    const hit = voices.find((v) => v.name.toLowerCase() === name.toLowerCase())
             || voices.find((v) => v.name.toLowerCase().includes(name.toLowerCase()));
    if (hit) return hit;
  }
  return (
    voices.find((v) => /en-GB/i.test(v.lang) && /male/i.test(v.name)) ||
    voices.find((v) => /en-GB/i.test(v.lang)) ||
    voices.find((v) => /^en/i.test(v.lang)) ||
    null
  );
}

/* Speaking, one piece at a time.
 *
 * The old version handed the whole reply over as a single utterance and
 * cut it at 800 characters, so a long answer was silently truncated
 * mid-sentence. Now the reply is split into sentences and queued: JARVIS
 * begins on the first one while the rest wait, nothing is dropped, and
 * stopping it is immediate because only the current sentence is in
 * flight. */
let speaking = { queue: [], active: false, token: 0 };

function stopSpeaking() {
  speaking.token += 1;
  speaking.queue = [];
  speaking.active = false;
  try { speechSynthesis.cancel(); } catch (e) {}
  setBusy(false);
}

function splitForSpeech(text) {
  // The same rule the server uses. Kept short here on purpose: the server
  // is the authority, and this is what runs when the reply is already in
  // hand and waiting a round trip to split it would be the slower path.
  const parts = text.replace(/\s+/g, " ").trim()
    .split(/(?<=[.!?…])\s+(?=[A-Z0-9"'\u00C0-\u024F])/);
  const out = [];
  for (const part of parts) {
    if (out.length && out[out.length - 1].length < 20) out[out.length - 1] += " " + part;
    else out.push(part);
  }
  if (out.length > 1 && out[out.length - 1].length < 20) {
    out[out.length - 2] += " " + out.pop();
  }
  return out.filter(Boolean);
}

// The browser check drives this directly. Named as the seam it is rather
// than reached through a mock reply, because what is being checked is the
// speaking, not the answering.
window.jarvisSpeak = (text, delivery) => speak(text, delivery);

async function speak(text, delivery) {
  if (!speakOn || !canSpeak || !text) return;
  await loadVoiceProfile();

  stopSpeaking();
  const token = speaking.token;
  loadVoices();

  const lang = langOf(text);
  const conf = settingsFor(delivery || "normal");
  const chosen = pickVoice(lang, conf.prefer);

  if (lang && !chosen) {
    voiceProblem(
      `No ${lang} voice is installed on this device, so JARVIS cannot ` +
      `read that reply aloud. On iPad: Settings → Accessibility → ` +
      `Spoken Content → Voices.`
    );
    return;
  }

  speaking.queue = splitForSpeech(text);
  speaking.active = true;
  next(token, chosen, conf, lang);
}

function next(token, chosen, conf, lang) {
  if (token !== speaking.token) return;      // a newer reply took over
  const line = speaking.queue.shift();
  if (!line) { speaking.active = false; setBusy(false); return; }

  let u;
  try { u = new SpeechSynthesisUtterance(line); }
  catch (e) { setBusy(false); return; }

  if (chosen) {
    // Setting .voice can throw if the browser hands back something it
    // will not accept. Losing the accent is a small loss; losing the
    // whole utterance because of it is not.
    try { u.voice = chosen; } catch (e) {}
    u.lang = chosen.lang;
  } else if (lang) {
    u.lang = lang;
  } else {
    u.lang = conf.lang || "en-GB";
  }
  u.rate = correctedRate(conf.rate ?? 0.85);
  u.pitch = conf.pitch ?? 0.92;
  u.volume = conf.volume ?? 1;

  const words = line.split(/\s+/).filter(Boolean).length;
  let started = 0;

  u.onstart = () => { started = performance.now(); setBusy(true); };
  u.onend = () => {
    if (started) calibrate(words, (performance.now() - started) / 1000);
    next(token, chosen, conf, lang);
  };
  u.onerror = () => {
    setBusy(false);
    speaking.queue = [];
    voiceProblem("The browser refused to play the voice.");
  };

  try { speechSynthesis.speak(u); }
  catch (e) { setBusy(false); voiceProblem("The browser refused to play the voice."); }
}

function voiceProblem(msg) {
  const el = $("convoNote");
  if (el) el.textContent = msg;
}

/* ------------------------------------------------------------ live voice */
/* The mic button now opens a live conversation: Gemini hears you and
 * answers in its own voice. That is a different thing from the browser
 * reading text aloud, and it is what handles Kannada, Hindi and Marathi
 * mixed into English -- the model hears the mixture directly instead of a
 * dictation engine guessing at one language and handing over text.
 *
 * The browser voice stays exactly as it was, for typed conversation and
 * for when live voice is unavailable. Neither replaces the other.
 */
let liveTurn = null;
// Set when live voice fails. Shutting down fires an "off" event straight
// after the error, and clearing the note there wiped the explanation
// before it could be read -- so the failure looked like nothing happening
// at all, which is the exact failure mode this project keeps producing.
let liveError = null;

function setupMic() {
  const btn = $("micBtn");
  btn.title = "Talk to JARVIS";
  btn.onclick = async () => {
    unlockSpeech();
    if (liveActive()) { stopLive(); return; }
    liveError = null;
    await startLive();
  };
}

document.addEventListener("jarvis:live", (e) => {
  const { state, detail } = e.detail;
  const btn = $("micBtn");
  const note = $("convoNote");

  if (state === "connecting") {
    btn.classList.add("rec");
    note.textContent = "Connecting to live voice…";
    setBusy(true);
    return;
  }
  if (state === "listening") {
    btn.classList.add("rec");
    btn.title = "Stop listening";
    // Says both things that matter: it is still listening without being
    // asked again, and how to make it stop.
    note.textContent = detail
      ? `Listening — ${detail}. Just keep talking; tap the mic to stop.`
      : "Listening — just keep talking. Tap the mic to stop.";
    setBusy(false);
    return;
  }
  if (state === "reconnecting") {
    note.textContent =
      `The connection dropped; picking the conversation back up (try ${detail})…`;
    setBusy(true);
    return;
  }
  if (state === "speaking") {
    // Still recording while JARVIS talks, so you can cut in. Echo
    // cancellation is what stops it hearing itself and interrupting
    // its own sentence.
    note.textContent = "JARVIS is speaking — talk over it to interrupt.";
    setBusy(true);
    return;
  }
  if (state === "off") {
    btn.classList.remove("rec");
    btn.title = "Talk to JARVIS";
    // Keep a failure on screen; only a clean stop clears it.
    if (!liveError) note.textContent = "";
    setBusy(false);
    liveTurn = null;
    refresh();
    return;
  }
  if (state === "error") {
    liveError = detail || "Live voice failed.";
    btn.classList.remove("rec");
    setBusy(false);
    note.textContent = liveError;
    // Also in the conversation, where you are actually looking. A note
    // under the input is easy to miss, and a voice failure that goes
    // unnoticed looks exactly like a voice feature that does not exist.
    addMsg("jarvis", liveError, "live voice", "err");
    return;
  }

  // Transcripts arrive in fragments, so each side's bubble is created
  // once and then appended to. A new bubble per fragment would shred one
  // sentence across a dozen lines.
  if (state === "heard" || state === "said") {
    const who = state === "heard" ? "you" : "jarvis";
    if (!liveTurn || liveTurn.who !== who) {
      liveTurn = { who, el: addMsg(who, "", who === "jarvis" ? "spoken" : null) };
    }
    const body = liveTurn.el.querySelector(".body");
    body.textContent += detail;
    convo.scrollTop = convo.scrollHeight;
    return;
  }
  if (state === "turn") liveTurn = null;
});

/* ------------------------------------------------------------ dashboard */
let lastReplyMs = null;

function renderDashboard(d) {
  $("ownerLine").textContent = d.owner || "";
  $("cfgOwner").textContent = d.owner || "—";

  loadMoney();
  const stopped = d.status.emergency_stop;
  $("statusText").textContent = stopped ? "STOPPED" : "ONLINE";
  $("statusText").classList.toggle("stopped", stopped);
  $("reactor").classList.toggle("stopped", stopped);
  $("stopBtn").textContent = stopped ? "Resume JARVIS" : "Emergency stop";
  $("stopBtn").classList.toggle("danger", !stopped);
  $("stopNote").textContent = stopped
    ? "JARVIS is refusing every request. Nothing has been deleted."
    : "Refuses every request until switched back on. Nothing is deleted.";

  $("sMessages").textContent = d.today.messages;
  $("sLearned").textContent = d.today.learning_runs;
  $("sSpend").textContent = "₹" + d.today.spend_inr.toFixed(2);
  $("sSpendNote").textContent =
    d.today.spend_inr === 0 ? "free tier — see BUDGET.md" : "estimated";

  // Gauges read against what actually reaches the model, so "full" means
  // "as much as JARVIS can use", not an invented capacity.
  const pct = d.budget.percent_used;
  // "0%" for a real but tiny spend reads as "nothing spent", which is the
  // wrong direction to be wrong about money.
  const pctLabel = pct === 0 ? "0%" : pct < 1 ? "<1%" : pct.toFixed(0) + "%";
  gauge("gBudget", "gBudgetTxt", pct, 100, pctLabel, d.budget.status);
  gauge("gFacts", "gFactsTxt", d.memory.facts, d.memory.facts_limit, String(d.memory.facts));
  gauge("gRecall", "gRecallTxt", d.memory.turns, d.memory.recall_turns_limit, String(d.memory.turns));

  $("memNote").textContent =
    `${d.memory.forgotten} forgotten · ₹${d.budget.spend_inr.toFixed(2)} of ` +
    `₹${d.budget.ceiling_inr.toFixed(0)} this month`;

  sparkline(d.history || []);

  $("tModel").textContent = d.status.model;
  $("tLast").textContent = lastReplyMs === null ? "—" : (lastReplyMs / 1000).toFixed(1) + "s";
  $("tMem").textContent = `${d.memory.facts}f / ${d.memory.turns}t`;

  $("cfgModel").textContent = `${d.status.provider} · ${d.status.model}`;
  $("cfgRecall").textContent = d.status.recall_enabled
    ? `on · last ${d.memory.recall_turns_limit} turns` : "off";
  $("cfgFacts").textContent = d.status.facts_enabled ? "on" : "off";

  // facts
  const fl2 = $("factsList");
  fl2.innerHTML = "";
  if (!d.facts.length) {
    fl2.innerHTML = "<li class='empty'>Nothing yet. Tell JARVIS something worth keeping.</li>";
  } else {
    for (const fact of d.facts) {
      const li = document.createElement("li");
      li.innerHTML =
        `<span class="cat">${esc(fact.category)}</span>` +
        `<span class="txt">${esc(fact.content)}</span>`;
      const x = document.createElement("button");
      x.className = "x"; x.textContent = "×"; x.title = "Forget this";
      x.onclick = async () => {
        if (!confirm("Make JARVIS forget this? It can be restored from the console.")) return;
        await api(`/v1/memories/${fact.id}/forget`, { method: "POST" });
        refresh();
      };
      li.appendChild(x);
      fl2.appendChild(li);
    }
  }

  // The Activity panel is gone -- a list of "Message answered · success"
  // rows told the owner nothing they had not just watched happen. The
  // audit log still records every one of them; GET /v1/audit reads it,
  // and the Tasks tab shows the work that actually matters.
}

// The audit log stores machine names. Nobody should have to learn them to
// read their own activity feed.
// A 270-degree arc starting at the lower left, the way a dial reads.
// 151 is the arc's length in this 32-radius circle; the rest of the
// circumference is the gap at the bottom.
const ARC = 151;
function gauge(arcId, textId, value, max, label, status) {
  const frac = max > 0 ? Math.max(0, Math.min(1, value / max)) : 0;
  const el = $(arcId);
  el.style.strokeDashoffset = ARC * (1 - frac);
  el.classList.toggle("warn", status === "warn_50" || status === "warn_80" || (!status && frac >= 0.8));
  el.classList.toggle("bad", status === "exceeded" || (!status && frac >= 1));
  $(textId).textContent = label;
}

// Messages per day for the past week. Drawn from the series the server
// sends, which includes quiet days as zero -- a chart that skipped them
// would draw a flattering line instead of a true one.
function sparkline(history) {
  const svg = $("spark");
  if (!history.length) { svg.innerHTML = ""; return; }
  const vals = history.map((h) => h.messages);
  const peak = Math.max(1, ...vals);
  const W = 300, H = 46, step = W / Math.max(1, history.length - 1);
  const y = (v) => H - 4 - (v / peak) * (H - 10);
  const pts = vals.map((v, i) => `${(i * step).toFixed(1)},${y(v).toFixed(1)}`);

  svg.innerHTML =
    `<defs><linearGradient id="sparkFill" x1="0" y1="0" x2="0" y2="1">
       <stop offset="0%" stop-color="#48c8ff" stop-opacity=".35"/>
       <stop offset="100%" stop-color="#48c8ff" stop-opacity="0"/>
     </linearGradient></defs>` +
    `<polygon points="0,${H} ${pts.join(" ")} ${W},${H}" fill="url(#sparkFill)"/>` +
    `<polyline points="${pts.join(" ")}" fill="none" stroke="url(#gSpark)" stroke-width="2"
       stroke-linejoin="round" stroke-linecap="round"/>` +
    vals.map((v, i) =>
      `<circle cx="${(i * step).toFixed(1)}" cy="${y(v).toFixed(1)}" r="${i === vals.length - 1 ? 3 : 1.8}"
        fill="${i === vals.length - 1 ? "#bfefff" : "#48c8ff"}"/>`).join("");

  const first = new Date(history[0].day);
  $("sparkFrom").textContent = first.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  $("sparkPeak").textContent = `peak ${peak}`;
}

async function refresh() {
  try {
    const res = await api("/v1/dashboard");
    if (!res.ok) return false;
    renderDashboard(await res.json());
    return true;
  } catch (e) { return false; }
}

/* ------------------------------------------------------------ messaging */
const convo = $("convo");
function addMsg(who, text, tail, cls = "") {
  if (convo.querySelector(".empty")) convo.innerHTML = "";
  const div = document.createElement("div");
  div.className = `msg ${who} ${cls}`;
  div.innerHTML =
    `<div class="from">${who === "you" ? "You" : "J.A.R.V.I.S"}</div>` +
    `<div class="body">${esc(text)}</div>` +
    (tail ? `<div class="tail">${esc(tail)}</div>` : "");
  convo.appendChild(div);
  convo.scrollTop = convo.scrollHeight;
  return div;
}

async function send() {
  // This is a real user gesture, which is the only moment iOS will let us
  // lift the speech restriction. Done here rather than only in the toggle
  // because the toggle may have been set on a previous visit and restored
  // from storage, with no tap involved at all.
  unlockSpeech();

  const input = $("msg");
  const text = input.value.trim();
  // An attachment on its own is a message: "read this" needs no words.
  if (!text && !attached) return;
  input.value = "";

  // Taken now, so a second message sent while this one is in flight does
  // not send the same document twice.
  const sending = attached;
  attached = null;
  showAttached();

  addMsg("you", sending ? `${text ? text + "\n" : ""}📎 ${sending.filename}` : text);
  setBusy(true);
  $("convoNote").textContent = sending
    ? `Reading ${sending.filename}…`
    : "Thinking… the first message after a break can take a minute while the server wakes.";

  try {
    const res = await api("/v1/message", {
      method: "POST",
      body: JSON.stringify({ text, attachment_id: sending ? sending.id : undefined }),
    });
    let data = {};
    try { data = await res.json(); } catch (e) {}

    if (res.ok) {
      lastReplyMs = data.total_ms;
      const msg = addMsg("jarvis", data.reply,
        `${data.provider} · ${data.recalled_turns} turns, ${data.recalled_facts} facts · ` +
        `${(data.total_ms / 1000).toFixed(1)}s`);
      speak(data.reply);
      // Only the reply is spoken. The offer is a decision, and a decision
      // read aloud as a wall of text is harder to act on than one sitting
      // on screen with two buttons under it.
      if (data.offer) addOffer(msg, data.offer);
      if (data.open) openIt(msg, data.open);
    } else {
      addMsg("jarvis", explain(res.status, data.detail || ""), null, "err");
    }
  } catch (e) {
    addMsg("jarvis", `Could not reach JARVIS: ${e.message}. It may still be waking up — wait a minute and try again.`, null, "err");
  } finally {
    setBusy(false);
    $("convoNote").textContent = "";
    refresh();
  }
}

// Plain English, and pointing at the real cause rather than the most
// likely-sounding one.
function explain(status, detail) {
  if (status === 401) return "You are not signed in. Reload the page and sign in again.";
  if (status === 403) return "Signed in, but not as the owner of this JARVIS.\n\n" + detail;
  if (status === 503 && /Emergency Stop/i.test(detail))
    return "JARVIS is stopped. Turn the emergency stop off in Settings to resume.";
  if (status === 502) {
    const retired = detail.match(/use\s+models\/([A-Za-z0-9._-]+)/);
    if (retired)
      return `Google has retired the model JARVIS uses and says to use ${retired[1]}.\n\n` +
             `Fix: Render → Environment → set GEMINI_MODEL to ${retired[1]} → Save.\n\n${detail}`;
    if (/RESOURCE_EXHAUSTED|quota|429/i.test(detail))
      return "The free daily quota for your Gemini key is used up. It resets every 24 hours.\n\n" + detail;
    if (/API_KEY_INVALID|PERMISSION_DENIED|UNAUTHENTICATED/i.test(detail))
      return "The API key is missing or wrong. Check GEMINI_API_KEY in Render.\n\n" + detail;
    return "JARVIS could not get an answer from the model.\n\n" + detail;
  }
  if (status >= 500)
    return "Something failed inside JARVIS.\n\n" + detail +
           "\n\nIf this mentions the database, the Supabase project may have paused — open its dashboard and restore it.";
  return `Unexpected response (HTTP ${status}).\n\n${detail}`;
}

/* ------------------------------------------------------------------ nav */
// In the landscape layout every panel is on screen at once, so "go to
// memory" had nothing to go to. Home and Settings remain; full screen is
// genuinely useful for leaving this up on a spare monitor.
const VIEWS = { home: null, tasks: "tasksView", agents: "agentsView",
                build: "buildView", media: "mediaView",
                settings: "settingsView" };
const homeGrid = () => document.querySelector(".grid");

function showView(name) {
  homeGrid().classList.toggle("hidden", name !== "home");
  for (const [view, id] of Object.entries(VIEWS)) {
    if (id) $(id).classList.toggle("hidden", view !== name);
  }
  if (name === "tasks") { loadWorkflows(); loadSchedules(); }
  if (name === "media") { loadBrands(); loadPieces(); }
  if (name === "agents") loadOrg();
  if (name === "build") loadBuilds();
}

$("nav").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-view]");
  if (!btn) return;

  // Check the view exists BEFORE marking the button active. The old order
  // highlighted first and then threw on an unknown name, which is the
  // worst possible outcome: the button looks selected and nothing opens,
  // and there is no clue on screen that anything went wrong. That is
  // exactly what a browser running new HTML against a cached older
  // app.js produced.
  const name = btn.dataset.view;
  if (!(name in VIEWS)) {
    console.error(`No such view: ${name}. This page's HTML is newer than ` +
                  `its script — reload to pick up the rest of the update.`);
    return;
  }

  [...$("nav").querySelectorAll("button[data-view]")].forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");
  showView(name);
});

$("fsBtn").onclick = async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  } catch (e) { /* Safari on iOS refuses; nothing useful to say about it */ }
};
document.addEventListener("fullscreenchange", () =>
  $("fsBtn").classList.toggle("active", !!document.fullscreenElement));

/* ------------------------------------------------------------- controls */
$("sendBtn").onclick = send;
$("msg").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });

$("speakToggle").checked = speakOn;
$("speakToggle").onchange = (e) => {
  speakOn = e.target.checked;
  try { localStorage.setItem("jarvis.speak", speakOn ? "1" : "0"); } catch (err) {}
  if (speakOn) {
    unlockSpeech();
    // Speaking immediately confirms it works, in this tap, rather than
    // leaving you to discover on the next reply that it does not.
    speak("Voice enabled.", "success");
  } else {
    stopSpeaking();
  }
};
$("voiceNote").textContent = canSpeak
  ? "Uses your device's built-in voice — no API, no cost, no limit. It " +
    "matches the language JARVIS replies in, if that voice is installed. " +
    "iPad: Settings → Accessibility → Spoken Content → Voices to add more."
  : "This browser has no speech engine, so JARVIS cannot speak here.";

$("voiceCheckBtn").onclick = async () => {
  const out = $("voiceOut");
  out.style.display = "block";
  out.textContent = "";
  // unlockSpeech inside this tap, so the check exercises the same
  // permission state a real reply would.
  unlockSpeech();
  await voiceCheck((line) => { out.textContent += line + "\n"; out.scrollTop = out.scrollHeight; });
  out.textContent += "\nCopy this whole block and send it to Claude.\n";
};

$("stopBtn").onclick = async () => {
  const stopping = $("stopBtn").textContent.startsWith("Emergency");
  if (stopping && !confirm("Stop JARVIS? It will refuse every request until you turn this off.")) return;
  await api("/v1/admin/emergency-stop", { method: "POST", body: JSON.stringify({ stop: stopping }) });
  refresh();
};

$("signOutBtn").onclick = async () => {
  await api("/auth/logout", { method: "POST" });
  location.reload();
};

/* -------------------------------------------------------------- opening */
// "Open YouTube." JARVIS runs on a server with no screen, so what opens
// is this browser, here.
//
// window.open() from inside a fetch callback is not a user gesture, and
// every popup blocker treats it accordingly -- iOS Safari most strictly.
// So the tab is attempted, and when it is blocked the reply grows a
// button instead. A tap on that IS a gesture and always works.
//
// The alternative, navigating this tab away with location.href, was
// rejected: it closes JARVIS to open something else, and on iOS coming
// back means a cold reload and signing in again.
// Whether THIS device has the JARVIS shortcut. Per device, not per
// account: the iPad has it, the laptop does not, and the server has no
// business knowing which browser this is. localStorage is exactly the
// right shape for that -- and it is a convenience, so a browser that
// refuses to store it just means the prompt appears again.
const HAS_SHORTCUT = "jarvis.shortcut";

function hasShortcut() {
  try { return localStorage.getItem(HAS_SHORTCUT) === "yes"; }
  catch (e) { return false; }
}

function setHasShortcut(yes) {
  try { localStorage.setItem(HAS_SHORTCUT, yes ? "yes" : "no"); }
  catch (e) { /* private window; it will ask again, which is fine */ }
}

// A timer, a reminder, a message, a light. None of those is a web
// address, and JARVIS has none of them -- it is a server in another
// room. Shortcuts is the hand on an iPhone or iPad.
function runShortcut(msg, action) {
  if (!hasShortcut()) {
    const row = document.createElement("div");
    row.className = "cost";
    row.style.marginTop = ".5rem";
    row.innerHTML =
      `JARVIS cannot ${esc(action.instruction)} by itself — it has no ` +
      `timer, no messages and no lights. On an iPhone or iPad a Shortcut ` +
      `can do it. <a href="/shortcut.html" target="_blank" rel="noopener">` +
      `How to set it up</a>, then `;
    const yes = document.createElement("button");
    yes.className = "btn";
    yes.textContent = "I've added it";
    yes.onclick = () => { setHasShortcut(true); runShortcut(msg, action); };
    row.append(yes);
    msg.append(row);
    return;
  }

  // The page builds the link, not the server, so the callback can carry
  // this exact address and bring him back where he was.
  const back = encodeURIComponent(location.href);
  const url = "shortcuts://x-callback-url/run-shortcut" +
    "?name=JARVIS&input=text" +
    "&text=" + encodeURIComponent(action.instruction) +
    "&x-success=" + back;

  // Same as a tab: try it, and if the browser refuses, leave something
  // to tap. A shortcut link opened without a gesture is blocked exactly
  // like a popup.
  let went = false;
  try { location.href = url; went = true; } catch (e) { /* offer below */ }
  if (went) return;

  const row = document.createElement("div");
  row.className = "row";
  row.style.marginTop = ".5rem";
  const go = document.createElement("button");
  go.className = "btn";
  go.textContent = "Run it";
  go.onclick = () => { location.href = url; };
  row.append(go);
  msg.append(row);
}

// Which browser opens a link is not a web page's decision.
//
// On iOS and Android, Chrome registers its own URL schemes --
// googlechrome:// for http, googlechromes:// for https -- so "in Chrome"
// can be honoured there. On Windows, macOS and Linux there is no
// equivalent: the operating system picks, and no page can override it.
//
// So the scheme is only used where it works. Emitting it everywhere,
// which the first version did, produces a link that silently does
// nothing on a laptop -- and "it says it opened and I can't see it" is
// the report this whole week has been about.
function canChooseBrowser() {
  const ua = navigator.userAgent || "";
  const iOS = /iPad|iPhone|iPod/.test(ua) ||
              (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  return iOS || /Android/.test(ua);
}

function addressFor(opening) {
  if (opening.browser !== "chrome" || !canChooseBrowser()) return opening.url;
  return opening.url
    .replace(/^https:\/\//, "googlechromes://")
    .replace(/^http:\/\//, "googlechrome://");
}

// Said once per session, when JARVIS is about to send him somewhere
// else, because the next thing that happens is the disappointment.
//
// A backgrounded tab on iOS is suspended: no microphone, no socket, no
// JavaScript. JARVIS cannot hear anything while he is in Spotify, and
// no amount of code changes that -- only a native app can hold a
// microphone in the background, and this is a web page on purpose.
// Saying so is better than him asking it something and waiting.
let saidAboutAway = false;

function onAnIPad() {
  const ua = navigator.userAgent || "";
  return /iPad/.test(ua) ||
         (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
}

function awayNote() {
  if (saidAboutAway) return "";
  saidAboutAway = true;

  // On an iPad this is not a limitation to apologise for -- it is a
  // layout choice he has not been told about.
  //
  // iPadOS does not fire the Page Visibility API when you move between
  // apps in Split View: neither is hidden, so neither is suspended and
  // JARVIS keeps listening. Full screen is the only arrangement where
  // it goes deaf. Saying "come back to this tab" was the right advice
  // for a phone and the wrong advice here -- it described the problem
  // instead of the way round it.
  if (onAnIPad()) {
    return " Put them side by side (drag this tab to the edge, or use " +
           "Split View) and JARVIS keeps listening while you use the " +
           "other app. Full screen is the only way it goes deaf.";
  }
  return " While you are in the other app JARVIS cannot hear you — " +
         "come back to this tab and it starts listening again by itself.";
}

// Said only when he asked for a browser this device cannot give him.
function browserNote(opening) {
  if (!opening.browser || opening.browser === "safari") return "";
  if (canChooseBrowser()) return "";
  return " This device decides which browser opens a link, not JARVIS — " +
         "on a computer only a program installed on it could choose.";
}

function openIt(msg, opening) {
  if (opening.kind === "shortcut") return runShortcut(msg, opening);

  const url = addressFor(opening);

  // Try it -- and then do not believe it.
  //
  // This used to be `if (tab) return;`, which is the standard way to
  // detect a blocked popup and is wrong on the device this is for. On
  // iOS Safari, window.open() called from a fetch callback can return a
  // perfectly good Window object that never navigates anywhere: the
  // popup is suppressed silently, every check says it worked, and the
  // page leaves nothing on screen. Which is precisely what "JARVIS still
  // can't open YouTube" looked like -- three times.
  //
  // So the result is discarded. A real link is always left behind, and
  // tapping a link is a gesture no browser refuses.
  try { window.open(url, "_blank", "noopener"); } catch (e) { /* the link below */ }

  const row = document.createElement("div");
  row.className = "row";
  row.style.marginTop = ".5rem";

  // An anchor, not a button. Long-press offers "open in new tab", the
  // address shows on hover, and it works with JavaScript disabled --
  // none of which is true of a button that calls window.open.
  const link = document.createElement("a");
  link.className = "btn";
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener";
  link.style.textDecoration = "none";
  link.textContent = `Open ${opening.site}`;

  const note = document.createElement("span");
  note.className = "note";
  note.textContent =
    (opening.browser === "chrome" && canChooseBrowser()
      ? "If Chrome did not open, tap this."
      : "If the tab did not open, tap this.") +
    browserNote(opening) + awayNote();

  row.append(link, note);
  msg.append(row);
}

/* ------------------------------------------------------------- start-up */
async function start() {
  const ok = await refresh();
  if (ok) {
    gate.classList.add("hidden");
    app.classList.remove("hidden");
    setupMic();
    // Keep the numbers honest without hammering a sleepy free-tier server.
    setInterval(refresh, 60000);
    loadMoney();
    return;
  }

  // Not signed in: show Google's own in-page button. It neither pops up
  // nor navigates, which is the only thing that works reliably on iPad.
  let cfg = {};
  try { cfg = await (await fetch("/public/firebase-config")).json(); } catch (e) {}

  if (!cfg.googleClientId) {
    $("gateMsg").innerHTML =
      "<span class='err'>GOOGLE_CLIENT_ID is not set on this server, so sign-in cannot be shown.</span>";
    $("gateHint").textContent = "See docs/DEPLOYMENT.md step 4f.";
    return;
  }

  $("gateMsg").textContent = "Sign in to continue.";
  const render = () => {
    if (!(window.google && window.google.accounts)) return setTimeout(render, 200);
    window.google.accounts.id.initialize({
      client_id: cfg.googleClientId,
      callback: async (r) => {
        $("gateMsg").textContent = "Signing in…";
        const res = await api("/auth/session", {
          method: "POST", body: JSON.stringify({ credential: r.credential }),
        });
        if (!res.ok) {
          const b = await res.json().catch(() => ({}));
          $("gateMsg").innerHTML = `<span class='err'>${esc(b.detail || "Sign-in refused (HTTP " + res.status + ").")}</span>`;
          return;
        }
        if (!(await refresh())) {
          $("gateMsg").innerHTML =
            "<span class='err'>Signed in, but your browser did not keep the login cookie.</span>";
          $("gateHint").textContent =
            "This is usually Private Browsing, or cookies blocked for this site. Try a normal tab.";
          return;
        }
        gate.classList.add("hidden");
        app.classList.remove("hidden");
        setupMic();
        setInterval(refresh, 60000);
      },
    });
    window.google.accounts.id.renderButton($("gsi"), {
      theme: "filled_black", size: "large", shape: "pill", text: "signin_with",
    });
  };
  render();
}
/* ---------------------------------------------------------------- tasks */
/* Plan, then run. Two steps on purpose: every step of a workflow is a real
 * model call against a real monthly budget, so nothing spends anything
 * until the plan has been read. The Run button appears only once there is
 * a plan on screen to approve.
 *
 * Progress is polled rather than streamed. The work happens in the server's
 * background, so the page can be closed and reopened without losing it --
 * which a streaming connection would not survive. */
let pendingPlan = null;      // the proposal awaiting approval
let watching = null;         // the workflow id being polled
let pollTimer = null;

const STEP_STATES = ["queued", "blocked", "running", "waiting_approval",
                     "completed", "failed", "cancelled"];
// Workflow statuses that mean nothing more will happen without a person.
const DONE = new Set(["completed", "failed", "cancelled", "waiting_approval"]);

function taskNote(text, tone = "") {
  const el = $("taskNote");
  el.textContent = text;
  el.style.color = tone === "bad" ? "var(--danger)"
                 : tone === "good" ? "var(--ok)" : "";
}

function showPlanButtons({ run = false, discard = false, plan = true } = {}) {
  $("planBtn").classList.toggle("hidden", !plan);
  $("runBtn").classList.toggle("hidden", !run);
  $("discardBtn").classList.toggle("hidden", !discard);
}

/* --- proposing ---------------------------------------------------------- */
async function proposePlan() {
  const objective = $("objective").value.trim();
  if (!objective) { taskNote("Say what you want done first.", "bad"); return; }

  stopPolling();
  $("planBtn").disabled = true;
  taskNote("Working out the steps…");
  $("planSteps").innerHTML = "";
  $("planWhy").textContent = "";
  $("planRejected").textContent = "";

  try {
    const res = await api("/v1/plans", {
      method: "POST", body: JSON.stringify({ objective }),
    });
    if (!res.ok) throw new Error(await problem(res));
    const plan = await res.json();
    pendingPlan = { objective, ...plan };
    renderPlan(plan);
  } catch (err) {
    taskNote(String(err.message || err), "bad");
    showPlanButtons({});
  } finally {
    $("planBtn").disabled = false;
  }
}

function renderPlan(plan) {
  $("planTitle").textContent = "The plan";
  $("planWhy").textContent = plan.reasoning || "";

  if (!plan.steps || !plan.steps.length) {
    $("planSteps").innerHTML =
      `<p class="note">Nothing JARVIS has can do this yet — so it is not going to pretend otherwise.</p>`;
    showPlanButtons({ discard: true });
    taskNote("No plan to run.");
  } else {
    $("planSteps").innerHTML = plan.steps.map((s, i) => `
      <div class="step">
        <div class="cap">${i + 1}. ${esc(s.capability)}</div>
        <div class="obj">${esc(s.objective)}</div>
        ${s.after && s.after.length
          ? `<div class="meta">after: ${esc(s.after.join(", "))}</div>` : ""}
      </div>`).join("");
    showPlanButtons({ run: true, discard: true });
    const n = plan.steps.length;
    taskNote(`${n} step${n === 1 ? "" : "s"}. Nothing has run and nothing has been spent.`);
  }

  // What the planner asked for and was refused. Shown rather than dropped:
  // a planner that silently discards what it could not use looks like it
  // agreed with you.
  $("planRejected").textContent = (plan.rejected && plan.rejected.length)
    ? `Refused: ${plan.rejected.join("; ")}`
    : "";
}

function discardPlan() {
  pendingPlan = null;
  stopPolling();
  $("planSteps").innerHTML = "";
  $("planWhy").textContent = "";
  $("planRejected").textContent = "";
  $("planTitle").textContent = "The plan";
  showPlanButtons({});
  taskNote("Discarded. Nothing ran.");
}

/* --- running ------------------------------------------------------------ */
async function runPlan() {
  if (!pendingPlan || !pendingPlan.steps || !pendingPlan.steps.length) return;
  $("runBtn").disabled = true;
  taskNote("Starting…");

  try {
    const res = await api("/v1/workflows", {
      method: "POST",
      body: JSON.stringify({
        objective: pendingPlan.objective,
        // The approved steps go back verbatim, so what runs is what was
        // read. Re-planning here would run something nobody approved.
        steps: pendingPlan.steps.map((s) => ({
          capability: s.capability, objective: s.objective,
          name: s.name || "", after: s.after || [],
        })),
      }),
    });
    if (!res.ok) throw new Error(await problem(res));
    const started = await res.json();
    pendingPlan = null;
    showPlanButtons({});
    watch(started.workflow_id);
  } catch (err) {
    taskNote(String(err.message || err), "bad");
  } finally {
    $("runBtn").disabled = false;
  }
}

async function problem(res) {
  // Reuse the message vocabulary the rest of the page already speaks: a
  // second explanation of the same 502 would be a second thing to keep
  // right.
  let detail = "";
  try { detail = (await res.json()).detail || ""; } catch (e) { detail = res.statusText; }
  return explain(res.status, String(detail));
}

function stopPolling() {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = null;
  watching = null;
}

async function watch(workflowId) {
  stopPolling();
  watching = workflowId;
  await pollOnce();
}

async function pollOnce() {
  const id = watching;
  if (!id) return;
  try {
    const res = await api(`/v1/workflows/${id}`);
    if (!res.ok) throw new Error(await problem(res));
    const data = await res.json();
    if (watching !== id) return;   // the user moved on while this was in flight
    renderWorkflow(data);

    // Poll until it is genuinely over, rather than while it looks busy.
    // A workflow is "planning" from the moment it is created and only
    // becomes "running" once a wave has settled, so watching for "running"
    // stops before the work has started.
    if (DONE.has(data.workflow.status)) {
      stopPolling();
      loadWorkflows();
    } else {
      pollTimer = setTimeout(pollOnce, 2000);
    }
  } catch (err) {
    taskNote(String(err.message || err), "bad");
    stopPolling();
  }
}

function renderWorkflow(data) {
  const wf = data.workflow, rows = data.tasks || [];
  $("planTitle").textContent = "Progress";
  $("planWhy").textContent = wf.objective || "";

  $("planSteps").innerHTML = rows.map((t, i) => {
    const state = STEP_STATES.includes(t.status) ? t.status : "";
    const bits = [];
    if (t.confidence != null) bits.push(`confidence ${Number(t.confidence).toFixed(2)}`);
    if (t.spend_inr != null) bits.push(`Rs.${Number(t.spend_inr).toFixed(2)}`);
    return `
      <div class="step ${state}">
        <div class="cap">${i + 1}. ${esc(t.capability)} — ${esc(t.status)}</div>
        <div class="obj">${esc(t.objective)}</div>
        ${bits.length ? `<div class="meta">${esc(bits.join(" · "))}</div>` : ""}
        ${t.failure_reason ? `<div class="meta" style="color:var(--danger)">${esc(t.failure_reason)}</div>` : ""}
        ${renderResult(t.result)}
      </div>`;
  }).join("") || `<p class="note">No tasks.</p>`;

  const spent = rows.reduce((sum, t) => sum + Number(t.spend_inr || 0), 0);
  const done = rows.filter((t) => t.status === "completed").length;
  taskNote(
    `${wf.status} — ${done}/${rows.length} done, Rs.${spent.toFixed(2)} spent.`,
    wf.status === "failed" ? "bad" : wf.status === "completed" ? "good" : "",
  );
  $("planRejected").textContent = wf.failure_reason || "";
}

function renderResult(result) {
  if (!result) return "";
  const out = result.output;
  let body = "";

  if (out && typeof out === "object" && Array.isArray(out.claims) && out.claims.length) {
    // Fact-checking: the verdict is the point, so it leads.
    body = out.claims.map((c) => `
      <div style="margin-top:.4rem">
        <span class="verdict ${esc(c.verdict)}">${esc(c.verdict)}</span>
        <span class="out" style="display:inline">${esc(c.claim)}</span>
        ${c.why ? `<div class="meta">${esc(c.why)}</div>` : ""}
        ${(c.sources || []).map(sourceLink).join("")}
      </div>`).join("");
  } else if (out && typeof out === "object") {
    body = `<p class="out">${esc(out.summary || JSON.stringify(out))}</p>`;
  } else if (out) {
    body = `<p class="out">${esc(String(out))}</p>`;
  }

  // A fact-check already shows each claim's own sources, and the task's
  // evidence list is those same links pooled together. Printing both
  // makes the panel look like there is twice as much support as there is.
  const perClaim = out && typeof out === "object" && Array.isArray(out.claims) && out.claims.length;
  const evidence = perClaim ? "" : (result.evidence || []).map(sourceLink).join("");
  const unresolved = (result.unresolved || []).length
    ? `<div class="meta" style="margin-top:.3rem">Unresolved: ${esc(result.unresolved.join("; "))}</div>`
    : "";
  return body + (evidence ? `<div style="margin-top:.3rem">${evidence}</div>` : "") + unresolved;
}

function sourceLink(source) {
  // Sources arrive as "Title — https://url" or a bare url. Splitting on the
  // last space keeps a title containing a dash intact.
  const match = String(source).match(/^(.*?)\s*—\s*(https?:\/\/\S+)$/);
  const title = match ? match[1] : source;
  const url = match ? match[2] : (String(source).startsWith("http") ? source : null);
  return url
    ? `<a class="src" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(title)}</a>`
    : `<span class="src">${esc(title)}</span>`;
}

/* --- history ------------------------------------------------------------ */
async function loadWorkflows() {
  try {
    const res = await api("/v1/workflows?limit=10");
    if (!res.ok) return;
    const rows = await res.json();
    $("wfList").innerHTML = rows.length ? rows.map((w) => `
      <div class="wf" data-wf="${esc(w.id)}">
        <span class="o">${esc(w.objective)}</span>
        <span class="s ${esc(w.status)}">${esc(w.status)}</span>
      </div>`).join("")
      : `<p class="note">Nothing yet.</p>`;
  } catch (e) { /* the panel is still usable without its history */ }
}

$("wfList").addEventListener("click", (e) => {
  const row = e.target.closest("[data-wf]");
  if (row) { pendingPlan = null; showPlanButtons({}); watch(row.dataset.wf); }
});

/* A spoken request that wanted doing.
 *
 * Out loud, the answer is spoken: JARVIS asked, and saying "yes" is what
 * starts it. The card is here so you can see what was heard and press it
 * if you would rather -- it is not the mechanism, and an earlier version
 * that made it the mechanism left every spoken "yes" reaching nothing. */
let voiceOffer = null;

document.addEventListener("jarvis:offer", (e) => {
  const msg = e.detail;

  // Spoken "open YouTube". Handled before the offer machinery, and
  // outside it: there is nothing to confirm, so there is no card, no
  // standing offer, and no yes to wait for.
  if (msg.type === "open") {
    openIt(addMsg("jarvis", msg.said || `Opening ${msg.site}.`, null), msg);
    return;
  }

  if (msg.type === "offer") {
    const row = addMsg("jarvis", "", null);
    row.querySelector(".body").remove();
    voiceOffer = addOffer(row, msg, { spoken: true });
    return;
  }
  if (!voiceOffer) return;

  if (msg.type === "offer_closed") {
    voiceOffer.innerHTML = `<div class="cost">Left it. Nothing was run.</div>`;
    voiceOffer = null;
  } else if (msg.type === "offer_running") {
    voiceOffer.className = "offer running";
    voiceOffer.innerHTML = `<div class="what">${esc(msg.objective)}</div>` +
                           `<div class="steps">Looking it up now…</div>`;
    showOffer(voiceOffer);
  } else if (msg.type === "offer_failed") {
    voiceOffer.className = "offer failed";
    voiceOffer.innerHTML = `<div class="cost">${esc(msg.message || "It did not finish.")}</div>`;
    voiceOffer = null;
  } else if (msg.type === "offer_done") {
    // JARVIS is about to say this aloud; the card carries the detail --
    // the sources, the cost -- that speech is a bad medium for.
    voiceOffer.className = "offer done";
    voiceOffer.innerHTML =
      `<div class="cost" style="margin:0 0 .2rem">${esc(msg.objective)}</div>` +
      `<div class="out">${esc(msg.summary || "Nothing came back.")}</div>`;
    showOffer(voiceOffer);
    voiceOffer = null;
    refresh();
  }
});

$("planBtn").onclick = proposePlan;
$("runBtn").onclick = runPlan;
$("discardBtn").onclick = discardPlan;

/* ---------------------------------------------------------------- offers */
/* JARVIS noticing that a message wanted doing rather than answering.
 *
 * The offer is the approval: accepting it plans and runs in one step. The
 * Tasks tab is where a plan is worth reading before it runs; asking twice
 * for one sentence of intent is friction, not safety. Nothing spends
 * anything until the button is pressed. */

function showOffer(box) {
  // Scroll to the TOP of the card, not the bottom of the conversation.
  // Otherwise the thing being offered -- or the answer that came back --
  // is pushed off the top and you are looking at its last line with no
  // idea what it was for.
  convo.scrollTop = Math.max(0, box.offsetTop - convo.offsetTop - 8);
}

function addOffer(afterEl, offer, { spoken = false } = {}) {
  const box = document.createElement("div");
  box.className = "offer";
  // What it would do is on the card, not assumed. There are two routes
  // now -- look something up, or research and draft a piece -- and they
  // cost very different amounts. JARVIS choosing the wrong one should be
  // something you can see and decline, not something you find out later.
  box.innerHTML =
    `<div class="what">I can ${esc(offer.does || "look this up properly")}: ` +
    `${esc(offer.objective)}</div>` +
    `<div class="cost">Cost: ${esc(offer.cost_note)}.` +
    (spoken ? " Just say yes — or use the buttons." : "") + `</div>` +
    `<div class="row">` +
    `<button class="btn go">Go ahead</button>` +
    `<button class="btn">No thanks</button>` +
    `</div>`;
  afterEl.appendChild(box);
  showOffer(box);

  const [go, no] = box.querySelectorAll("button");
  no.onclick = () => {
    box.innerHTML = `<div class="cost">Left it. Nothing was run.</div>`;
  };
  go.onclick = () => (offer.kind === "make"
    ? makeOffer(box, offer.objective)
    : runOffer(box, offer.objective));
  return box;
}

/* Making a piece is not a planned workflow.
 *
 * It goes to the Media Director, which runs a fixed chain with gates --
 * verification can stop it before a word is written, the strategist can
 * decide against it, the reviewer can send it back. The planner is
 * deliberately not allowed to assemble that chain for itself, so this
 * cannot go through the same endpoint as a look-up. */
async function makeOffer(box, topic) {
  box.className = "offer running";
  box.innerHTML = `<div class="what">${esc(topic)}</div>` +
                  `<div class="steps">Researching, verifying, then drafting…</div>`;
  try {
    const res = await api("/v1/media/produce", {
      method: "POST", body: JSON.stringify({ topic }),
    });
    if (!res.ok) throw new Error(await problem(res));
    const started = await res.json();
    if (!started.piece_id || started.state === "failed") {
      box.className = "offer failed";
      box.innerHTML = `<div class="what">${esc(topic)}</div>` +
        `<div class="cost">${esc(started.reason || "It could not be started.")}</div>`;
      return;
    }
    await followPiece(box, topic, started.piece_id);
  } catch (err) {
    box.className = "offer failed";
    box.innerHTML = `<div class="what">${esc(topic)}</div>` +
                    `<div class="cost">${esc(String(err.message || err))}</div>`;
  }
}

async function followPiece(box, topic, pieceId) {
  for (let tick = 0; tick < 150; tick++) {
    const res = await api(`/v1/media/pieces/${pieceId}`);
    if (!res.ok) throw new Error(await problem(res));
    const piece = await res.json();

    if (piece.state !== "producing") {
      renderPieceResult(box, topic, piece);
      refresh();
      return;
    }
    const steps = box.querySelector(".steps");
    if (steps) steps.textContent = "Researching, verifying, then drafting…";
    await new Promise((r) => setTimeout(r, 3000));
  }
  const steps = box.querySelector(".steps");
  if (steps) steps.textContent = "Still going. Open the Media tab to watch the rest.";
}

function renderPieceResult(box, topic, piece) {
  // Four of the five outcomes are not "here is your piece", and three of
  // those are the system working correctly. A chain that stopped because
  // verification refuted a claim is a good outcome, and showing it in red
  // as a failure would teach exactly the wrong lesson.
  const good = piece.state === "ready";
  const neutral = ["declined", "stopped", "rejected"].includes(piece.state);
  box.className = `offer ${good ? "done" : neutral ? "" : "failed"}`;

  const title = piece.title || piece.topic || topic;
  const hook = piece.package && piece.package.hook;

  box.innerHTML =
    `<div class="what">${esc(title)}</div>` +
    (hook ? `<div class="out">${esc(hook)}</div>` : "") +
    `<div class="cost">${esc(PIECE_WORDS[piece.state] || piece.state)}` +
    (piece.reason ? ` — ${esc(piece.reason)}` : "") + `</div>` +
    `<div class="cost">Rs.${Number(piece.spend_inr || 0).toFixed(2)} billed · ` +
    `Rs.${Number(piece.shadow_inr || 0).toFixed(2)} at paid rates · ` +
    `<a class="src" href="#" data-open-piece="${esc(piece.id)}">open it on the Media tab</a></div>`;
  showOffer(box);

  const link = box.querySelector("[data-open-piece]");
  if (link) link.onclick = (e) => {
    e.preventDefault();
    openMediaPiece(link.dataset.openPiece);
  };
}

// The answer to "where is it, then". Nothing is displayed in the
// conversation itself: the piece lives on the Media tab, and this opens
// it there rather than leaving you to find it.
function openMediaPiece(pieceId) {
  [...$("nav").querySelectorAll("button[data-view]")].forEach((b) => b.classList.remove("active"));
  const tab = document.querySelector('button[data-view="media"]');
  if (tab) tab.classList.add("active");
  openPieceId = pieceId;
  showView("media");
  showPiece(pieceId);
}

async function runOffer(box, objective) {
  box.className = "offer running";
  box.innerHTML = `<div class="what">${esc(objective)}</div>` +
                  `<div class="steps">Working out the steps…</div>`;
  try {
    const res = await api("/v1/workflows", {
      method: "POST", body: JSON.stringify({ objective }),
    });
    if (!res.ok) throw new Error(await problem(res));
    const started = await res.json();
    if (started.status === "failed") {
      box.className = "offer failed";
      box.innerHTML = `<div class="what">${esc(objective)}</div>` +
        `<div class="cost">${esc(started.failure_reason || "Nothing registered can do this.")}</div>`;
      return;
    }
    await followOffer(box, objective, started.workflow_id);
  } catch (err) {
    box.className = "offer failed";
    box.innerHTML = `<div class="what">${esc(objective)}</div>` +
                    `<div class="cost">${esc(String(err.message || err))}</div>`;
  }
}

async function followOffer(box, objective, workflowId) {
  // Polled, not streamed: the work runs on the server, so closing the tab
  // does not cancel it, and reopening picks it up again from Tasks.
  for (let tick = 0; tick < 150; tick++) {
    const res = await api(`/v1/workflows/${workflowId}`);
    if (!res.ok) throw new Error(await problem(res));
    const data = await res.json();
    const rows = data.tasks || [];
    const done = rows.filter((t) => t.status === "completed").length;

    if (DONE.has(data.workflow.status)) {
      renderOfferResult(box, objective, data);
      refresh();
      return;
    }
    box.querySelector(".steps").innerHTML =
      `<b>${done}/${rows.length || "?"}</b> steps done — ` +
      esc(rows.map((t) => t.capability).join(" → ") || "starting");
    await new Promise((r) => setTimeout(r, 2000));
  }
  box.querySelector(".steps").textContent =
    "Still running. Open the Tasks tab to watch the rest.";
}

function renderOfferResult(box, objective, data) {
  const rows = data.tasks || [];
  const spent = rows.reduce((sum, t) => sum + Number(t.spend_inr || 0), 0);
  const failed = data.workflow.status !== "completed";
  box.className = `offer ${failed ? "failed" : "done"}`;

  // The last step speaks: in a chain it is the one that saw everything
  // before it. Its own summary is the answer worth showing.
  const last = [...rows].reverse().find((t) => t.result && t.result.output);
  const out = last && last.result.output;
  let body = "";
  if (out && typeof out === "object" && Array.isArray(out.claims) && out.claims.length) {
    body = out.claims.map((c) => `
      <div style="margin-top:.35rem">
        <span class="verdict ${esc(c.verdict)}">${esc(c.verdict)}</span>
        <span style="font-size:.88rem">${esc(c.claim)}</span>
        ${c.why ? `<div class="steps">${esc(c.why)}</div>` : ""}
        ${(c.sources || []).map(sourceLink).join("")}
      </div>`).join("");
  } else if (out) {
    const text = (typeof out === "object" ? (out.summary || JSON.stringify(out)) : String(out));
    body = `<div class="out">${esc(text)}</div>` +
           ((last.result.evidence || []).map(sourceLink).join(""));
  } else {
    body = `<div class="cost">${esc(data.workflow.failure_reason || "Nothing came back.")}</div>`;
  }

  // The objective goes small once the work is done: it was the question,
  // and the answer is what the owner came back for. The conversation box
  // is short, so whichever line leads gets most of the space.
  box.innerHTML =
    `<div class="cost" style="margin:0 0 .2rem">${esc(objective)}</div>` + body +
    `<div class="cost">${failed ? "Did not finish" : "Done"} · ` +
    `Rs.${spent.toFixed(2)} · <a class="src" href="#" data-open-tasks="1">see the steps</a></div>`;
  showOffer(box);

  const link = box.querySelector("[data-open-tasks]");
  if (link) link.onclick = (e) => {
    e.preventDefault();
    [...$("nav").querySelectorAll("button[data-view]")].forEach((b) => b.classList.remove("active"));
    document.querySelector('button[data-view="tasks"]').classList.add("active");
    showView("tasks");
    watch(data.workflow.id);
  };
}

/* ------------------------------------------------------------- schedules */
/* Work that happens without being asked.
 *
 * Deliberately plain: an objective and a time. Cron syntax is a thing
 * people get wrong and then cannot debug, and "every morning at seven" is
 * what is actually wanted. */

const DAY_NAMES = ["", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function whenText(s) {
  const at = `${String(s.hour).padStart(2, "0")}:${String(s.minute).padStart(2, "0")}`;
  const days = (s.days_of_week || []).length
    ? s.days_of_week.map((d) => DAY_NAMES[d]).join(" ")
    : "daily";
  return `${days} ${at}`;
}

function outcomeText(s) {
  if (!s.last_run_at) return "not run yet";
  const when = new Date(s.last_run_at).toLocaleString(undefined,
    { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  return `last: ${s.last_outcome || "?"} · ${when}`;
}

async function loadSchedules() {
  try {
    const res = await api("/v1/schedules");
    if (!res.ok) return;
    const rows = await res.json();
    $("schList").innerHTML = rows.length ? rows.map((s) => `
      <div class="sch ${s.enabled ? "" : "paused"}" data-id="${esc(s.id)}">
        <span class="o">${esc(s.objective)}<small>${esc(outcomeText(s))}</small></span>
        <span class="when">${esc(whenText(s))}</span>
        <button data-act="toggle">${s.enabled ? "pause" : "resume"}</button>
        <button class="x" data-act="delete" title="Remove">×</button>
      </div>`).join("")
      : `<p class="note" style="margin:0">Nothing scheduled. JARVIS only acts when you ask.</p>`;
  } catch (e) { /* the panel is still usable without its list */ }
}

$("schAdd").onclick = async () => {
  const objective = $("schObjective").value.trim();
  const [hour, minute] = ($("schTime").value || "07:00").split(":").map(Number);
  if (!objective) { $("schNote").textContent = "Say what should be done."; return; }

  $("schAdd").disabled = true;
  try {
    const res = await api("/v1/schedules", {
      method: "POST",
      body: JSON.stringify({ objective, hour, minute, days: [], max_per_day: 2 }),
    });
    if (!res.ok) throw new Error(await problem(res));
    $("schObjective").value = "";
    $("schNote").textContent =
      "Scheduled. It runs on its own: twice a day at most, and no more than " +
      "20 unattended runs a day in total. (The 60%-of-budget limit only " +
      "bites once you are on a paid model — Gemini's free tier records ₹0, " +
      "so a share of your ceiling is never reached.) On this free server it " +
      "fires when JARVIS is awake; if it was asleep, it runs when you next " +
      "open this page and says how late it was.";
    loadSchedules();
  } catch (err) {
    $("schNote").textContent = String(err.message || err);
  } finally {
    $("schAdd").disabled = false;
  }
};

$("schList").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const row = btn.closest("[data-id]");
  const id = row.dataset.id;

  if (btn.dataset.act === "delete") {
    if (!confirm("Remove this schedule?")) return;
    await api(`/v1/schedules/${id}`, { method: "DELETE" });
  } else {
    const paused = row.classList.contains("paused");
    await api(`/v1/schedules/${id}/enabled?enabled=${paused}`, { method: "POST" });
  }
  loadSchedules();
});

/* ----------------------------------------------------------------- money */
/* What came in and what went out.
 *
 * Almost every row gets here by being said out loud -- "got forty
 * thousand from the Bengaluru shoot" -- and read out of the exchange by
 * the same pass that learns facts. This panel is where you check it, and
 * where you remove one that was misheard: forty thousand and four
 * thousand sound alike, and a ledger you cannot correct is one you stop
 * trusting. */

const rupees = (n) => `₹${Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;

async function loadMoney() {
  try {
    const res = await api("/v1/money?limit=12");
    if (!res.ok) return;
    const { totals, recent } = await res.json();

    $("moneyIn").textContent = rupees(totals.month_in);
    $("moneyOut").textContent = rupees(totals.month_out);
    $("moneyNet").textContent = rupees(totals.month_net);

    $("moneyList").innerHTML = recent.length ? recent.map((m) => `
      <div class="mv" data-id="${esc(m.id)}">
        <span class="w">${esc(m.what)}<small>${esc(m.occurred_on)}${
          m.category ? " · " + esc(m.category) : ""}</small></span>
        <span class="a ${esc(m.direction)}">${m.direction === "in" ? "+" : "−"}${
          esc(rupees(m.amount_inr))}</span>
        <button title="Remove">×</button>
      </div>`).join("")
      : `<p class="note" style="margin:0">Nothing yet. Tell JARVIS: “got ₹40,000 from the Bengaluru shoot”.</p>`;

    // Said where the figures are, not in a help page: these totals are
    // real but partial, and a partial total read as a complete one is
    // the worst kind of wrong number.
    $("moneyNote").textContent = totals.entries
      ? "Only what you have told JARVIS — there is no bank feed."
      : "";
  } catch (e) { /* the dashboard is still usable without it */ }
}

$("moneyList").addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  const row = btn.closest("[data-id]");
  if (!confirm("Remove this entry? Totals will change.")) return;
  await api(`/v1/money/${row.dataset.id}`, { method: "DELETE" });
  loadMoney();
});

start();

/* -------------------------------------------------------------- media */
/* The media company, on one screen.
 *
 * Three columns and one rule: nothing on this page publishes anything.
 * A scan looks, a production runs five agents and stops at a package,
 * and the only thing that moves a piece past "ready" is the owner's
 * finger on a button.
 *
 * Both cost figures are shown side by side and never added. On the free
 * tier the real one is zero and true, and the shadow one is the only
 * thing that makes two pieces comparable -- showing one without the
 * other would be either useless or misleading. */

let mediaBrandsLoaded = false;
let openPieceId = null;
let mediaPoll = null;

async function loadBrands() {
  if (mediaBrandsLoaded) return;
  try {
    const res = await api("/v1/media/brands");
    if (!res.ok) return;
    const rows = await res.json();
    const options = rows
      .map((b) => `<option value="${esc(b.id)}">${esc(b.name)}</option>`)
      .join("");
    $("mediaBrand").innerHTML = options;
    $("makeBrand").innerHTML = options;
    mediaBrandsLoaded = true;
  } catch (e) { /* the select stays empty; the server still defaults */ }
}

$("scanBtn").onclick = async () => {
  const theme = $("mediaTheme").value.trim();
  if (!theme) { $("scanNote").textContent = "Say what to look at."; return; }

  $("scanBtn").disabled = true;
  $("scanNote").textContent = "Looking…";
  $("oppList").innerHTML = `<p class="empty">Searching and ranking…</p>`;
  try {
    const res = await api("/v1/media/scan", {
      method: "POST",
      body: JSON.stringify({ theme, brand: $("mediaBrand").value || undefined }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "The scan would not start.");
    watchScan(data.workflow_id);
  } catch (e) {
    $("scanNote").textContent = e.message;
    $("oppList").innerHTML = `<p class="empty">Nothing scanned.</p>`;
    $("scanBtn").disabled = false;
  }
};

function watchScan(workflowId) {
  let tries = 0;
  const tick = async () => {
    tries += 1;
    try {
      const res = await api(`/v1/media/scan/${workflowId}`);
      if (!res.ok) throw new Error("Lost track of the scan.");
      const data = await res.json();

      if (data.state === "running" || data.state === "planning") {
        // Bounded, so a scan that never finishes shows as a stuck scan
        // rather than a page that polls for ever.
        if (tries > 60) throw new Error("The scan is taking too long. It may still finish — check Pieces later.");
        setTimeout(tick, 3000);
        return;
      }

      $("scanBtn").disabled = false;
      renderOpportunities(data);
    } catch (e) {
      $("scanBtn").disabled = false;
      $("scanNote").textContent = e.message;
    }
  };
  tick();
}

function renderOpportunities(data) {
  const found = data.opportunities || [];
  if (!found.length) {
    // A finished scan with an empty queue is an answer. Saying nothing
    // here is how "nothing was worth covering" reads as "it broke".
    $("oppList").innerHTML = `<p class="empty">${
      data.nothing_worth_covering
        ? "Nothing found worth spending research money on. That is a result."
        : esc(data.reason || "The scan did not finish.")
    }</p>`;
    $("scanNote").textContent = "";
    return;
  }

  $("scanNote").textContent = `${found.length} worth a look.`;
  $("oppList").innerHTML = found.map((o, i) => `
    <div class="step opp" data-opp="${i}">
      <span class="score">${(o.score ?? 0).toFixed(2)}</span>
      <div class="cap">${esc(o.brand || "ai_media")}</div>
      <div class="obj">${esc(o.title || "")}</div>
      <div class="meta">${esc(o.why_now || "")}</div>
      <div class="meta">${esc(o.reason_to_exist || "")}</div>
      <div class="meta" style="color:var(--cyan-dim)">Tap to make this</div>
    </div>`).join("");

  $("oppList").dataset.found = JSON.stringify(found);
}

$("oppList").addEventListener("click", (e) => {
  const card = e.target.closest("[data-opp]");
  if (!card) return;
  let found = [];
  try { found = JSON.parse($("oppList").dataset.found || "[]"); } catch (err) {}
  const opportunity = found[Number(card.dataset.opp)];
  if (opportunity) startProduction(opportunity.title, opportunity.brand);
});

/* Make one, without asking the model for permission.
 *
 * Every previous route to this went through the conversation: the model
 * had to notice the request, mark it with the right kind, and have the
 * offer survive the registry check. Three things that can each quietly
 * fail, and between them they meant a week of being told work was
 * happening when none was. This asks the model nothing. */
$("makeBtn").onclick = async () => {
  const topic = $("makeTopic").value.trim();
  if (!topic) { $("makeNote").textContent = "Say what it should be about."; return; }

  $("makeBtn").disabled = true;
  $("makeNote").textContent = "Starting…";
  try {
    const res = await api("/v1/media/produce", {
      method: "POST",
      body: JSON.stringify({ topic, brand: $("makeBrand").value || undefined }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "It would not start.");
    if (!data.piece_id) throw new Error(data.reason || "Nothing was created.");

    $("makeTopic").value = "";
    $("makeNote").textContent =
      "Started. It is in the list, and Right now on the home screen shows " +
      "each step as it goes.";
    openPieceId = data.piece_id;
    loadPieces();
    loadNow();
  } catch (e) {
    $("makeNote").textContent = e.message;
  } finally {
    $("makeBtn").disabled = false;
  }
};

async function startProduction(topic, brand) {
  $("scanNote").textContent = `Making: ${topic}`;
  try {
    const res = await api("/v1/media/produce", {
      method: "POST",
      body: JSON.stringify({ topic, brand: brand || $("mediaBrand").value || undefined }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "It would not start.");
    if (data.piece_id) openPieceId = data.piece_id;
    loadPieces();
  } catch (e) {
    $("scanNote").textContent = e.message;
  }
}

const PIECE_WORDS = {
  producing: "being made", ready: "waiting for you", needs_you: "needs you",
  declined: "decided against", stopped: "stopped on a false claim",
  rejected: "rejected by review", approved: "approved by you",
  discarded: "discarded", failed: "did not finish",
};

async function loadPieces() {
  try {
    const [res, econRes] = await Promise.all([
      api("/v1/media/pieces?limit=20"),
      api("/v1/media/economics?days=30"),
    ]);
    if (!res.ok) return;
    const rows = await res.json();

    $("pieceList").innerHTML = rows.length ? rows.map((p) => `
      <div class="wf" data-piece="${esc(p.id)}">
        <span class="o">${esc(p.title || p.topic)}</span>
        <span class="s ${esc(p.state)}">${esc(PIECE_WORDS[p.state] || p.state)}</span>
      </div>`).join("")
      : `<p class="empty">Nothing made yet.</p>`;

    if (econRes.ok) {
      const e = await econRes.json();
      $("econNote").textContent =
        `${e.pieces} in 30 days · ${e.approved} approved · ${e.not_made} decided against · ` +
        `Rs.${e.spend_inr.toFixed(2)} billed · Rs.${e.shadow_inr.toFixed(2)} at paid rates`;
    }

    // Something still running means the list is not final. Polled rather
    // than pushed because a production takes minutes and the phone may
    // well be locked for most of them.
    const busy = rows.some((p) => p.state === "producing");
    clearTimeout(mediaPoll);
    if (busy && !$("mediaView").classList.contains("hidden")) {
      mediaPoll = setTimeout(loadPieces, 5000);
    }
    if (openPieceId) showPiece(openPieceId);
  } catch (e) { /* the panel is still usable without its list */ }
}

$("pieceList").addEventListener("click", (e) => {
  const row = e.target.closest("[data-piece]");
  if (row) { openPieceId = row.dataset.piece; showPiece(openPieceId); }
});

async function showPiece(pieceId) {
  try {
    const res = await api(`/v1/media/pieces/${pieceId}`);
    if (!res.ok) return;
    const p = await res.json();

    $("pieceTitle").textContent = p.title || p.topic;
    $("pieceState").textContent =
      `${PIECE_WORDS[p.state] || p.state}${p.reason ? " — " + p.reason : ""}`;

    const pack = p.package || {};
    const sections = pack.sections || [];
    const review = p.review || {};

    let body = "";
    // The steps first while it is being made, because that is the whole
    // question at that point. A piece that shows only "being made" looks
    // identical whether it is working or has stalled.
    if (p.state === "producing" && (p.steps || []).length) {
      const done = p.steps.filter((s) => s.status === "completed").length;
      const started = p.created_at ? new Date(p.created_at) : null;
      const mins = started ? Math.round((Date.now() - started) / 60000) : null;
      body += `<div class="beat"><div class="name">${done} of ${p.steps.length} steps` +
              (mins !== null ? ` · started ${mins} min ago` : "") + `</div>` +
              p.steps.map((s) =>
                `<div class="step ${esc(s.status)}">` +
                `<div class="cap">${esc(s.capability)}</div>` +
                `<div class="meta">${esc(s.status)}` +
                (s.failure_reason ? ` — ${esc(s.failure_reason)}` : "") +
                `</div></div>`).join("") + `</div>`;
      if (mins !== null && mins > 5) {
        body += `<p class="note">A run normally takes a few minutes. This one
                 has not, so something is likely stuck rather than slow.</p>`;
      }
    }
    if (pack.hook) body += `<div class="beat"><div class="name">Hook</div><div class="out">${esc(pack.hook)}</div></div>`;
    if (sections.length) {
      body += sections.map((s) => `
        <div class="beat">
          <div class="name">${esc(s.beat || "")}</div>
          <div class="out">${esc(s.text || "")}</div>
          ${(s.cites || []).map((c) => `<span class="src">rests on: ${esc(c)}</span>`).join("")}
        </div>`).join("");
    }
    if (pack.thumbnail_concept) {
      body += `<div class="beat"><div class="name">Thumbnail</div><div class="out">${esc(pack.thumbnail_concept)}</div></div>`;
    }
    if (review.must_fix && review.must_fix.length) {
      body += `<div class="beat"><div class="name">The reviewer says fix</div>` +
              review.must_fix.map((m) => `<div class="out">• ${esc(m)}</div>`).join("") +
              `</div>`;
    }
    if (review.unsupported_claims && review.unsupported_claims.length) {
      body += `<div class="beat"><div class="name">Not supported by the research</div>` +
              review.unsupported_claims.map((m) => `<div class="out">• ${esc(m)}</div>`).join("") +
              `</div>`;
    }
    if (!body) {
      body = `<p class="empty">${esc(p.reason || "Nothing was written.")}</p>`;
    }

    body += `<div class="cost" style="margin-top:.5rem">Rs.${Number(p.spend_inr).toFixed(2)} billed · ` +
            `Rs.${Number(p.shadow_inr).toFixed(2)} at paid rates</div>`;
    $("pieceBody").innerHTML = body;

    // Only a piece that is actually waiting gets buttons. Offering a
    // decision on something already decided is how a second tap looks
    // like it did nothing.
    const decidable = p.state === "ready" || p.state === "needs_you";
    $("pieceButtons").classList.toggle("hidden", !decidable);
    $("pieceNote").textContent = decidable
      ? "Approving records your decision. It does not publish anything — nothing here can."
      : (p.decided_by ? `You decided this on ${new Date(p.decided_at).toLocaleString()}.` : "");
  } catch (e) { /* leave whatever was on screen */ }
}

async function decidePiece(decision) {
  if (!openPieceId) return;
  $("approveBtn").disabled = $("discardPieceBtn").disabled = true;
  try {
    const res = await api(`/v1/media/pieces/${openPieceId}/${decision}`, { method: "POST" });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      $("pieceNote").textContent = data.detail || "That could not be decided.";
    }
  } finally {
    $("approveBtn").disabled = $("discardPieceBtn").disabled = false;
    loadPieces();
  }
}

$("approveBtn").onclick = () => decidePiece("approve");
$("discardPieceBtn").onclick = () => decidePiece("discard");

/* --------------------------------------------------------------- build */
/* A brief becoming a branch you can read.
 *
 * Two steps, deliberately. Planning costs one model call and produces
 * something to read; writing costs a call per file and produces a diff to
 * review. You decide in between whether it is worth it.
 *
 * Nothing here merges. JARVIS writes in a separate git worktree, never
 * the running deployment, and approving records that you read it. The
 * branch stays yours. */

let openBuild = null;
let buildPoll = null;

async function loadBuilds() {
  try {
    const res = await api("/v1/dev");
    if (!res.ok) return;
    const data = await res.json();

    // Said up front. A brief that becomes a plan and then discovers there
    // is no git repository has spent money for nothing.
    const repo = data.repo || {};
    $("repoNote").textContent = repo.usable
      ? `Building from ${repo.path} on ${repo.branch}.`
      : repo.why || "This deployment cannot build changes.";
    $("planBuildBtn").disabled = !repo.usable;

    const rows = data.requests || [];
    $("buildList").innerHTML = rows.length ? rows.map((r) => `
      <div class="wf" data-build="${esc(r.id)}">
        <span class="o">${esc(r.title)}</span>
        <span class="s ${esc(r.state)}">${esc(BUILD_WORDS[r.state] || r.state)}</span>
      </div>`).join("") : `<p class="empty">Nothing proposed yet.</p>`;

    // Attachments can be planned from directly, so a brief never has to
    // be pasted twice.
    const files = await api("/v1/attachments?limit=10");
    if (files.ok) {
      const list = await files.json();
      $("buildFrom").innerHTML =
        `<option value="">Use the text above</option>` +
        list.map((f) => `<option value="${esc(f.id)}">${esc(f.filename)}</option>`).join("");
    }

    loadProviders();
    renderGovernor(data.governor || {});
    renderRecovery(data.recovery || {});
    loadScientist();
    loadTrials();
    loadReach();

    clearTimeout(buildPoll);
    if (rows.some((r) => r.state === "building") &&
        !$("buildView").classList.contains("hidden")) {
      buildPoll = setTimeout(loadBuilds, 5000);
    }
    if (openBuild) showBuild(openBuild);
  } catch (e) { /* the tab is still readable without a refresh */ }
}

// Model provider keys.
//
// The box is always empty on load and there is no endpoint that could
// fill it. What is shown is whether a key is set and where it came from
// -- never the key, not even masked, because a masked key is still most
// of a key and its length names the provider.
const PROVIDER_NAMES = { openai: "ChatGPT", gemini: "Gemini", claude: "Claude" };

async function loadProviders() {
  try {
    const res = await api("/v1/settings/providers");
    if (!res.ok) return;
    const data = await res.json();
    const rows = Object.entries(data.providers || {});
    $("providerList").innerHTML = rows.map(([name, p]) => `
      <div class="wf">
        <span class="o">${esc(PROVIDER_NAMES[name] || name)}</span>
        <span class="s ${p.configured ? "completed" : ""}">${
          p.configured
            ? esc(p.source === "dashboard" ? "set here" : "set in .env")
            : "not set"
        }</span>
      </div>`).join("");
    if (!$("keyNote").textContent) $("keyNote").textContent = data.said || "";
  } catch (e) { /* the rest of the tab still works */ }
}

$("saveKeyBtn").onclick = async () => {
  const provider = $("keyProvider").value;
  const key = $("keyValue").value.trim();
  if (!key) { $("keyNote").textContent = "Paste a key first."; return; }

  $("saveKeyBtn").disabled = true;
  $("keyNote").textContent = `Saving and testing the ${PROVIDER_NAMES[provider]} key…`;
  try {
    const res = await api("/v1/settings/providers", {
      method: "POST",
      body: JSON.stringify({
        provider, api_key: key, model: $("keyModel").value.trim() || null,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      // Cleared whether or not the key worked. It is on the server now;
      // leaving it on screen only leaves it on screen.
      $("keyValue").value = "";
      $("keyNote").textContent = data.works
        ? `Saved. ${data.said} ${data.summary || ""}`
        : `Saved, but it did not work: ${data.said}`;
      loadProviders();
    } else {
      $("keyNote").textContent = data.detail || "That could not be saved.";
    }
  } catch (e) {
    $("keyNote").textContent = "That could not be saved.";
  } finally {
    $("saveKeyBtn").disabled = false;
  }
};

// The autonomy ceiling. Shown as what it permits, not as a number: "2 —
// normal development" means nothing on its own, and this is the setting
// least safe to misread.
function renderGovernor(gov) {
  const levels = (gov.levels || []).filter((l) => l.level <= 3);
  const select = $("govCeiling");
  if (!select) return;
  select.innerHTML = levels.map((l) => `
    <option value="${l.level}"${l.level === gov.ceiling ? " selected" : ""}>
      ${l.level === 0 ? "Ask me about everything"
                      : `Approve up to ${esc(l.name)} alone`}
    </option>`).join("");
  $("govMeans").textContent = gov.means || "";
  $("govNever").textContent =
    `The protected core — the Constitution, permissions, authentication, ` +
    `the budget, the Governor itself — is never changed this way, at any ` +
    `setting.`;
}

$("govCeiling").onchange = async (e) => {
  const level = Number(e.target.value);
  $("govMeans").textContent = "Saving…";
  try {
    const res = await api("/v1/dev/governor/ceiling", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ level }),
    });
    if (res.ok) renderGovernor(await res.json());
    else $("govMeans").textContent = "That could not be saved.";
  } catch (err) { $("govMeans").textContent = "That could not be saved."; }
};

// A new version of an agent being tried against the live one.
//
// Both arms are shown with their run counts, because the interesting
// case is the one where the answer is "not enough yet" -- and a panel
// that only showed a verdict would make that look like nothing is
// happening.
async function loadTrials() {
  const panel = $("trialPanel");
  if (!panel) return;
  try {
    const res = await api("/v1/trials");
    if (!res.ok) return;
    const data = await res.json();
    const live = data.running;

    $("trialSaid").textContent = data.said || "";
    $("trialButtons").classList.toggle("hidden", !live);
    $("trialNote").textContent = live
      ? `A share of this agent's work goes to the new version. It needs ` +
        `${data.runs_needed_each} runs on each side before a difference ` +
        `means anything, and ${Math.round(data.margin * 100)} points of ` +
        `daylight before it counts as one.`
      : "";

    if (!live || !live.verdict) { $("trialArms").innerHTML = ""; return; }
    const v = live.verdict;
    const arm = (a, label) => kv(
      `${label} (v${a.version})`,
      `${percent(a.success_rate)} of ${a.runs} run${a.runs === 1 ? "" : "s"}` +
      (a.enough_to_judge ? "" : ` ${unknown("— too few to judge")}`));
    $("trialArms").innerHTML =
      kv("Capability", esc(live.capability)) +
      arm(v.candidate, "The new one") +
      arm(v.baseline, "The one doing the job");
  } catch (e) { /* the tab is readable without it */ }
}

for (const [id, decision, confirm] of [
  ["promoteTrialBtn", "promote", "Promote the new version?"],
  ["rejectTrialBtn", "reject", "Reject it and keep the current version?"],
  ["abandonTrialBtn", "abandon", "Stop the trial without deciding?"],
]) {
  const btn = $(id);
  if (!btn) continue;
  btn.onclick = async () => {
    const res0 = await api("/v1/trials");
    if (!res0.ok) return;
    const live = (await res0.json()).running;
    if (!live || !window.confirm(confirm)) return;
    $("trialNote").textContent = "Saving…";
    try {
      const res = await api(
        `/v1/trials/${encodeURIComponent(live.capability)}/${decision}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason: "" }),
        });
      if (res.ok) { loadTrials(); loadBuilds(); }
      else {
        // The refusal is the useful part: "the numbers do not support
        // promoting this" is the panel earning its place.
        const said = await res.json().catch(() => ({}));
        $("trialNote").textContent = said.detail || "That could not be done.";
      }
    } catch (e) { $("trialNote").textContent = "That could not be done."; }
  };
}

// What JARVIS may run on the machine, and when it may search the web.
// Read-only: both are server settings, and a switch here that silently
// did nothing would be worse than no switch.
async function loadReach() {
  const panel = $("reachPanel");
  if (!panel) return;
  try {
    const res = await api("/v1/computer");
    if (res.ok) {
      const c = await res.json();
      $("computerSaid").textContent = c.said || "";
      $("computerList").innerHTML = c.enabled
        ? (c.runs_without_asking || []).map((a) =>
            kv(esc(a.what), `<span class="mono">${esc(a.command)}</span>` +
              (a.available ? "" : ` ${unknown("— not installed here")}`))).join("")
        : "";
      $("computerElse").textContent = c.enabled
        ? c.anything_else
        : c.how_to_enable || "";
    }
    const web = await api("/v1/search-policy");
    if (web.ok) {
      const w = await web.json();
      $("searchSaid").textContent = `Searching the web: ${w.means}`;
    }
    // Asked, not read off a setting. "Configured" and "running" are
    // different facts and the useful one is the second.
    const local = await api("/v1/local-model");
    if (local.ok) {
      const l = await local.json();
      $("localSaid").innerHTML = esc(l.said) +
        (l.how ? ` <span class="unknown">${esc(l.how)}</span>` : "") +
        (l.configured && !l.up
          ? ` <span class="tag">not answering</span>` : "");
    }
  } catch (e) { /* the tab is readable without it */ }
}

function renderRecovery(rec) {
  const note = $("recoveryNote");
  const cmd = $("recoveryCmd");
  if (!note) return;
  note.textContent = rec.command
    ? `${rec.means} ${rec.honestly}`
    : (rec.why_not || rec.means || "");
  if (rec.command) {
    cmd.textContent = rec.command;
    cmd.style.display = "";
  } else {
    cmd.style.display = "none";
  }
}

async function loadScientist() {
  try {
    const res = await api("/v1/dev/scientist");
    if (!res.ok) return;
    const data = await res.json();
    const found = data.findings || [];
    $("labList").innerHTML = found.length ? found.map((f, i) => `
      <div class="wf">
        <span class="o">${esc(f.hypothesis)}</span>
        <button class="btn" data-lab="${i}">Propose a fix</button>
      </div>`).join("") : `<p class="empty">${esc(data.said || "Nothing yet.")}</p>`;
    $("labNote").textContent = data.note || "";
  } catch (e) { /* the panel is optional */ }
}

$("labList").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-lab]");
  if (!btn) return;
  btn.disabled = true;
  btn.textContent = "Planning…";
  try {
    const res = await api("/v1/dev/scientist/propose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index: Number(btn.dataset.lab) }),
    });
    if (res.ok) { const made = await res.json(); openBuild = made.id; }
    await loadBuilds();
  } catch (err) { btn.textContent = "That did not work"; }
});

const BUILD_WORDS = {
  planned: "planned", building: "writing…", proposed: "waiting for you",
  failed: "did not finish", approved: "approved", discarded: "discarded",
  // The Governor's own verdicts, worded so they are never mistaken for
  // the owner's. "Refused" is not a failure -- it is the boundary working.
  refused: "refused by the Governor",
};

$("buildList").addEventListener("click", (e) => {
  const row = e.target.closest("[data-build]");
  if (row) { openBuild = row.dataset.build; showBuild(openBuild); }
});

$("planBuildBtn").onclick = async () => {
  const brief = $("buildBrief").value.trim();
  const attachment = $("buildFrom").value;
  if (!brief && !attachment) {
    $("buildNote").textContent = "Paste a brief, or choose an attached one.";
    return;
  }
  $("planBuildBtn").disabled = true;
  $("buildNote").textContent = "Reading the code and working out what it would take…";
  try {
    const res = await api("/v1/dev/plan", {
      method: "POST",
      body: JSON.stringify({ brief, attachment_id: attachment || undefined }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "It would not plan.");
    openBuild = data.id;
    $("buildBrief").value = "";
    $("buildNote").textContent = data.state === "failed"
      ? data.reason
      : "Planned. Read it, then decide whether to have it written.";
    loadBuilds();
  } catch (err) {
    $("buildNote").textContent = err.message;
  } finally {
    $("planBuildBtn").disabled = false;
  }
};

function colourDiff(text) {
  return esc(text).split("\n").map((line) => {
    if (/^\+\+\+|^---/.test(line)) return `<span class="at">${line}</span>`;
    if (line.startsWith("+")) return `<span class="add">${line}</span>`;
    if (line.startsWith("-")) return `<span class="del">${line}</span>`;
    if (line.startsWith("@@")) return `<span class="at">${line}</span>`;
    return line;
  }).join("\n");
}

async function showBuild(id) {
  try {
    const res = await api(`/v1/dev/${encodeURIComponent(id)}`);
    if (!res.ok) return;
    const r = await res.json();

    $("buildTitle").textContent = r.title;
    $("buildState").textContent =
      `${BUILD_WORDS[r.state] || r.state}${r.reason ? " — " + r.reason : ""}`;

    const plan = r.plan || {};
    let body = "";
    if (plan.summary) body += `<div class="out">${esc(plan.summary)}</div>`;
    if ((plan.files || []).length) {
      body += `<div class="sect">Files</div>` + plan.files.map((f) =>
        `<div class="perm">${f.new ? "+" : "~"} ${esc(f.path)}` +
        `<span class="unknown"> — ${esc(f.why || "")}</span></div>`).join("");
    }
    if ((plan.steps || []).length) {
      body += `<div class="sect">Steps</div>` +
        plan.steps.map((x) => `<div class="perm">• ${esc(x)}</div>`).join("");
    }
    if ((plan.risk || []).length) {
      body += `<div class="sect">What could break</div>` +
        plan.risk.map((x) => `<div class="perm">• ${esc(x)}</div>`).join("");
    }
    // The most useful part of a plan, and the part a system that wants to
    // look capable would leave out.
    if ((plan.cannot || []).length) {
      body += `<div class="sect">What it cannot do</div>` +
        plan.cannot.map((x) => `<div class="perm">✕ ${esc(x)}</div>`).join("");
    }

    if (r.tests_passed !== null && r.tests_passed !== undefined) {
      body += `<div class="sect">Tests</div>` +
        `<div class="tests ${r.tests_passed ? "pass" : "fail"}">` +
        `${r.tests_passed ? "The tests pass." : "The tests FAIL. Read them before merging."}</div>`;
      if (r.tests_output) {
        body += `<div class="diff">${esc(r.tests_output.slice(-2500))}</div>`;
      }
    }

    if (r.branch) {
      body += `<div class="sect">Branch</div>` +
        `<div class="out">${esc(r.branch)} · ${r.files_changed} file(s)</div>`;
    }
    if (r.diff) {
      body += `<div class="sect">Diff</div><div class="diff">${colourDiff(r.diff)}</div>`;
    }
    body += `<div class="cost" style="margin-top:.5rem">Rs.${Number(r.spend_inr || 0).toFixed(2)} billed · ` +
            `Rs.${Number(r.shadow_inr || 0).toFixed(2)} at paid rates</div>`;

    $("buildDetail").innerHTML = body || `<p class="empty">${esc(r.reason || "Nothing to show.")}</p>`;

    const canWrite = r.state === "planned" || r.state === "failed";
    const canDecide = r.state === "proposed";
    $("buildButtons").classList.toggle("hidden", !canWrite && !canDecide);
    $("writeItBtn").hidden = !canWrite;
    $("approveBuildBtn").hidden = !canDecide;
    $("discardBuildBtn").hidden = !canDecide;
    $("buildFooter").textContent = canDecide
      ? "Approving records that you read it. JARVIS does not merge its own changes — the branch is yours."
      : r.decided_by ? `You decided this on ${new Date(r.decided_at).toLocaleString()}.`
      : "Writing costs one model call per file and produces a diff to review.";
  } catch (e) { /* leave whatever was on screen */ }
}

$("writeItBtn").onclick = async () => {
  if (!openBuild) return;
  $("writeItBtn").disabled = true;
  try {
    const res = await api(`/v1/dev/${encodeURIComponent(openBuild)}/build`,
                          { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "It would not start.");
    $("buildState").textContent = data.note;
    loadBuilds();
  } catch (err) {
    $("buildState").textContent = err.message;
  } finally {
    $("writeItBtn").disabled = false;
  }
};

async function decideBuild(decision) {
  if (!openBuild) return;
  $("approveBuildBtn").disabled = $("discardBuildBtn").disabled = true;
  try {
    const res = await api(`/v1/dev/${encodeURIComponent(openBuild)}/${decision}`,
                          { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "That could not be decided.");
    $("buildFooter").textContent = data.note;
  } catch (err) {
    $("buildFooter").textContent = err.message;
  } finally {
    $("approveBuildBtn").disabled = $("discardBuildBtn").disabled = false;
    loadBuilds();
  }
}

$("approveBuildBtn").onclick = () => decideBuild("approve");
$("discardBuildBtn").onclick = () => decideBuild("discard");

/* --------------------------------------------------------- attachments */
/* Handing JARVIS a document instead of typing it out.
 *
 * The file is uploaded and read on its own, then rides along with the
 * next message as an id. It is shown as a chip under the box rather than
 * as a turn in the conversation, because nothing has been said yet and
 * drawing it as a turn would imply it had.
 *
 * What it never does is act on its own. JARVIS reads it and says what it
 * says; anything it asks for comes back as an ordinary offer with a
 * button. That matters more now that JARVIS can change its own code: a
 * file that could start work would be a path from something on a phone
 * straight to something running on the server. */

let attached = null;

$("attachBtn").onclick = () => $("attachFile").click();
$("attachedClear").onclick = () => { attached = null; showAttached(); };

function showAttached(state, note) {
  const chip = $("attachedChip");
  chip.classList.toggle("hidden", !attached && !note);
  chip.className = `attached${!attached && !note ? " hidden" : ""}${state ? " " + state : ""}`;
  $("attachedName").textContent = note || (attached ? attached.filename : "");
  $("attachedSize").textContent = attached && !note
    ? `${attached.chars.toLocaleString()} chars${attached.pages ? ` · ${attached.pages}p` : ""}`
    : "";
  $("attachedClear").hidden = !attached;
}

$("attachFile").addEventListener("change", async (e) => {
  const file = e.target.files && e.target.files[0];
  e.target.value = "";              // so the same file can be picked twice
  if (!file) return;

  attached = null;
  showAttached("working", `Reading ${file.name}…`);
  try {
    const form = new FormData();
    form.append("file", file);
    // No Content-Type header: the browser must set the multipart boundary
    // itself, and api() would override it with application/json.
    const res = await fetch("/v1/attachments", {
      method: "POST", body: form, credentials: "same-origin",
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "That file could not be read.");

    attached = data;
    showAttached();
    $("convoNote").textContent = data.already_had_it
      ? "Already had that one. Send a message and JARVIS will read it again."
      : data.note;
    $("msg").focus();
  } catch (err) {
    attached = null;
    showAttached("failed", String(err.message || err));
    // The chip carries the reason, so it survives until the next attempt
    // rather than vanishing before it has been read.
    $("attachedClear").hidden = false;
    $("attachedClear").onclick = () => { showAttached(); $("attachedClear").onclick = () => { attached = null; showAttached(); }; };
  }
});

/* ----------------------------------------------------------- right now */
/* What is actually happening, on the screen the owner is already looking
 * at.
 *
 * The question that could not be answered was "is anything running at
 * all". JARVIS said the Media Director was working on it; there was no
 * script; and from outside there was no way to tell whether nothing had
 * started or something had stalled. This is that answer, refreshed while
 * anything is in flight and hidden entirely when nothing is -- a panel
 * that says "nothing" all day is a panel you stop reading. */

let nowPoll = null;

async function loadNow() {
  try {
    const res = await api("/v1/activity");
    if (!res.ok) return;
    const live = await res.json();

    const rows = [
      ...live.pieces.map((p) => ({
        who: "media", what: `${p.topic} — ${p.done}/${p.steps} steps`,
        when: p.since, stalled: p.stalled, piece: p.id,
      })),
      ...live.running.map((t) => ({
        who: (t.capability || "").split(".").pop(),
        what: t.objective, when: t.since, stalled: t.stalled,
      })),
      ...live.queued.map((t) => ({
        who: (t.capability || "").split(".").pop(),
        what: t.objective, when: "waiting", stalled: false,
      })),
    ];

    $("nowPanel").classList.toggle("hidden", !rows.length);
    if (rows.length) {
      $("nowList").innerHTML = rows.map((r) => `
        <div class="live${r.stalled ? " stalled" : ""}"${
          r.piece ? ` data-now-piece="${esc(r.piece)}"` : ""}>
          <span class="who">${esc(r.who || "?")}</span>
          <span class="what">${esc(r.what || "")}</span>
          <span class="when">${esc(r.when)}</span>
        </div>`).join("");
      $("nowNote").textContent = live.stalled
        ? "Longer than a run should take. This is stuck, not slow — it will " +
          "be picked back up within a minute or two, or you can start it again."
        : "";
    }

    clearTimeout(nowPoll);
    nowPoll = setTimeout(loadNow, rows.length ? 5000 : 20000);
  } catch (e) { /* the dashboard is fine without it */ }
}

$("nowList").addEventListener("click", (e) => {
  const row = e.target.closest("[data-now-piece]");
  if (row) openMediaPiece(row.dataset.nowPiece);
});

loadNow();

/* -------------------------------------------------------------- agents */
/* The organisation, drawn from the registry.
 *
 * There is no list of agents in this file. Every node comes from
 * /v1/org, which reads the Agent Registry and the task record, so an
 * agent registered, reassigned, degraded or retired changes this page
 * without anybody editing it. That is the whole point: a hand-maintained
 * diagram is out of date the first time the system changes and nobody
 * notices for a month.
 *
 * An indented tree rather than a drawn graph, deliberately. Boxes and
 * lines look impressive at nine agents and become an unreadable tangle at
 * ninety, need pan and zoom on a phone, and shrink text until it cannot
 * be read. An indented tree collapses, searches and scrolls, and reads
 * the same at any size. */

let orgRoot = null;
let orgSelected = null;
const orgOpen = new Set();          // ids whose children are showing
let orgFilter = "all";
let orgQuery = "";
let orgPoll = null;

// Above this many agents the tree opens collapsed to the first level.
// Small systems should show everything; large ones should not dump two
// hundred rows on you and call it an overview.
const COLLAPSE_ABOVE = 20;

async function loadOrg() {
  try {
    const res = await api("/v1/org");
    if (!res.ok) return;
    const data = await res.json();
    orgRoot = data.root;

    if (!orgOpen.size) {
      orgOpen.add(orgRoot.id);
      const many = (data.counts.agents || 0) > COLLAPSE_ABOVE;
      if (!many) openEverything(orgRoot);
      else for (const c of orgRoot.children) if (c.children.length) orgOpen.add(c.id);
    }

    const c = data.counts;
    $("orgCounts").textContent =
      `${c.agents} agents · ${c.working} working · ${c.waiting} waiting · ` +
      `${c.idle} idle` +
      (c.degraded ? ` · ${c.degraded} degraded` : "") +
      (c.experimental ? ` · ${c.experimental} experimental` : "") +
      (c.disabled ? ` · ${c.disabled} disabled` : "");

    drawOrg();
    if (orgSelected) showAgent(orgSelected);

    // Kept current while you are looking at it, and only while you are.
    // "What is this agent doing right now" is the question the page
    // exists to answer, and an answer from when the tab was opened is
    // not an answer to it.
    clearTimeout(orgPoll);
    if (!$("agentsView").classList.contains("hidden")) {
      orgPoll = setTimeout(loadOrg, c.working || c.waiting ? 6000 : 20000);
    }
  } catch (e) { /* the page is still readable without a refresh */ }
}

function openEverything(node) {
  if (node.children.length) orgOpen.add(node.id);
  node.children.forEach(openEverything);
}

function matches(node) {
  const q = orgQuery.trim().toLowerCase();
  const hitQ = !q || [node.name, node.id, node.domain, node.role]
    .some((f) => (f || "").toLowerCase().includes(q));
  const hitF =
    orgFilter === "all" ? true :
    orgFilter === "working" ? node.state === "working" :
    node.lifecycle === orgFilter;
  // A coordinator is a heading, not a match: it stays whenever anything
  // below it stays, and is never a result in its own right.
  return hitQ && (hitF || node.kind === "coordinator" || node.kind === "orchestrator");
}

function keep(node) {
  return matches(node) || node.children.some(keep);
}

function drawOrg() {
  if (!orgRoot) return;
  const searching = orgQuery.trim() || orgFilter !== "all";
  const rows = [];

  const walk = (node, depth) => {
    if (searching && !keep(node)) return;
    // Searching reveals: a match three levels down is useless if the
    // branch holding it is shut.
    const open = searching || orgOpen.has(node.id);
    const kids = node.children.length;
    rows.push(`
      <div class="node ${esc(node.kind)}${orgSelected === node.id ? " selected" : ""}"
           data-node="${esc(node.id)}" style="padding-left:${.4 + depth * .95}rem">
        <span class="twist ${kids ? (open ? "open" : "") : "leaf"}" data-twist="${esc(node.id)}">▶</span>
        <span class="nm">${esc(node.name)}</span>
        <span class="rl">${esc(node.role || "")}</span>
        <span class="dot ${esc(node.state)}" title="${esc(node.state)}"></span>
        <span class="lc ${esc(node.lifecycle === "active" ? node.health : node.lifecycle)}">${
          esc(node.lifecycle === "active" ? node.state : node.lifecycle)}</span>
      </div>`);
    if (open) node.children.forEach((child) => walk(child, depth + 1));
  };

  walk(orgRoot, 0);
  $("orgTree").innerHTML = rows.length ? rows.join("")
    : `<p class="empty">Nothing matches.</p>`;
}

$("orgTree").addEventListener("click", (e) => {
  const twist = e.target.closest("[data-twist]");
  if (twist) {
    const id = twist.dataset.twist;
    if (orgOpen.has(id)) orgOpen.delete(id); else orgOpen.add(id);
    drawOrg();
    return;
  }
  const row = e.target.closest("[data-node]");
  if (row) { orgSelected = row.dataset.node; drawOrg(); showAgent(orgSelected); }
});

$("agentSearch").addEventListener("input", (e) => { orgQuery = e.target.value; drawOrg(); });
$("expandAll").onclick = () => { if (orgRoot) { openEverything(orgRoot); drawOrg(); } };
$("collapseAll").onclick = () => { orgOpen.clear(); if (orgRoot) orgOpen.add(orgRoot.id); drawOrg(); };
$("agentFilters").addEventListener("click", (e) => {
  const chip = e.target.closest("[data-filter]");
  if (!chip) return;
  orgFilter = chip.dataset.filter;
  [...$("agentFilters").querySelectorAll(".chipbtn")].forEach(
    (b) => b.classList.toggle("on", b === chip));
  drawOrg();
});

const kv = (k, v) => `<div class="kv"><span class="k">${esc(k)}</span><span class="v">${v}</span></div>`;
const unknown = (why) => `<span class="unknown">${esc(why)}</span>`;
// The ledger rounds to whole rupees; an agent's cost is often a
// fraction of one, so this section needs two decimal places.
const costInr = (n) => `Rs.${Number(n || 0).toFixed(2)}`;
const percent = (n) => (n === null || n === undefined ? null : `${Math.round(n * 100)}%`);

async function showAgent(id) {
  try {
    const res = await api(`/v1/org/${encodeURIComponent(id)}`);
    if (!res.ok) return;
    const d = await res.json();

    $("agentName").textContent = d.identity.name;
    $("agentRole").textContent = d.role;

    $("agentDetail").innerHTML =
      (d.summary ? domainSummary(d) : agentBody(d));
  } catch (e) { /* leave whatever was on screen */ }
}

/* JARVIS itself and any coordinator: a board-level summary rather than a
   pretend agent with pretend metrics. */
function domainSummary(d) {
  const s = d.summary;
  let html = `<div class="sect">Identity</div>` +
    kv("Id", esc(d.identity.id)) +
    (d.identity.domain ? kv("Domain", esc(d.identity.domain)) : "") +
    kv("Registered agent", d.identity.routable ? "yes" : "no — this is not a model call");

  html += `<div class="sect">Right now</div>` +
    kv("Agents below", s.agents) +
    kv("Working", s.working) +
    kv("Idle", s.idle ?? "—") +
    (s.waiting ? kv("Waiting on you", s.waiting) : "") +
    (s.degraded ? kv("Degraded", s.degraded) : "") +
    kv("Tasks today", s.tasks_today + (s.failed_today ? ` (${s.failed_today} failed)` : "")) +
    kv("Success rate, 30 days", percent(s.success_rate) ?? unknown("not enough runs yet"));

  html += `<div class="sect">Cost, 30 days</div>` +
    kv("Billed", costInr(s.spend_30d_inr)) +
    kv("At paid rates", costInr(s.shadow_30d_inr)) +
    `<p class="note">Two figures, never added. The first is money that was
     actually charged; the second is what the same work would have cost on
     a paid model, which is the only one that compares two pieces of work
     while the first is zero.</p>`;

  if (d.reports && d.reports.length) {
    html += `<div class="sect">Reports</div>` + d.reports.map((r) =>
      kv(r.name, `${esc(r.state || "")} · ${esc(r.lifecycle)}`)).join("");
  }
  return html;
}

function agentBody(d) {
  const i = d.identity, p = d.performance, e = d.economics, a = d.activity;

  let html = `<div class="sect">Identity</div>` +
    kv("Capability", esc(i.capability)) +
    kv("Agent id", esc(i.agent_id || "—")) +
    kv("Domain", esc(i.domain || "—")) +
    kv("Reports to", esc(i.supervisor || "JARVIS")) +
    kv("Version", `v${i.version}`) +
    kv("Lifecycle", `${esc(i.lifecycle)}${i.routable ? "" : " — not routable"}`);

  if (d.responsibilities.length) {
    html += `<div class="sect">Responsibilities</div>` +
      d.responsibilities.map((r) => `<div class="perm">• ${esc(r)}</div>`).join("");
  }

  html += `<div class="sect">Models</div>` +
    kv("Asks for", d.models.tiers.map(esc).join(", ")) +
    kv("Which is, today", d.models.allowed.map((m) => `${esc(m.tier)}: ${esc(m.model)}`).join("<br>")) +
    kv("Last used", d.models.recent
        ? `${esc(d.models.recent.model)} (${esc(d.models.recent.tier)})`
        : unknown("has not run yet")) +
    `<p class="note">An agent asks for a tier, never a model name. What a
     tier means today comes from configuration, so a provider retiring a
     name changes one setting rather than every agent.</p>`;

  if (d.trial) {
    const t = d.trial;
    html += `<div class="sect">A new version is being tried</div>` +
      kv("Version being tried", `v${t.candidate_version} — taking ` +
         `${Math.round(t.share * 100)}% of this agent's work`) +
      kv("Against", `v${t.baseline_version}, which is doing the rest`) +
      `<p class="note">${esc(t.said)} Promote or reject it on the Build tab.</p>`;
  }

  const m = d.models.measured;
  if (m && m.rows.length) {
    html += `<div class="sect">Which model is good at this (${m.window_days} days)</div>` +
      m.rows.map((r) =>
        kv(esc(r.model), `${percent(r.success_rate)} of ${r.runs} run${r.runs === 1 ? "" : "s"}` +
          (r.enough_to_judge ? "" : ` ${unknown("— too few to judge")}`) +
          (r.model === m.struggling ? ` <span class="tag">routed around</span>` : "") +
          (r.avg_latency_ms ? `<span class="unknown"> · ${(r.avg_latency_ms / 1000).toFixed(1)}s</span>` : ""))
      ).join("") +
      `<p class="note">${m.struggling
        ? `Below ${Math.round(m.poor_below * 100)}% over ${m.enough_runs} runs or more, JARVIS
           raises the tier for this job rather than keep spending on a model that
           is not getting it right. It never lowers one on this evidence, and never
           overrules a model you named yourself.`
        : `Nothing here is failing enough to route around. It takes
           ${m.enough_runs} runs before JARVIS will judge a model at all — a
           handful of results is not evidence.`}</p>`;
  }

  html += `<div class="sect">Permissions</div>` +
    d.permissions.can.map((c) =>
      `<div class="perm"><span class="y">✓</span> ${esc(c.what)}` +
      (c.needs_approval ? ` <span class="tag">still needs your yes</span>` : "") +
      `</div>`).join("") +
    d.permissions.cannot.map((c) =>
      `<div class="perm"><span class="n">✕</span> ${esc(c.what)}` +
      (c.never_delegated ? ` <span class="tag">never delegated to any agent</span>` : "") +
      `</div>`).join("");

  html += `<div class="sect">How it has gone (30 days)</div>` +
    (p.runs
      ? kv("Runs", p.runs) +
        kv("Succeeded", p.enough_to_judge
            ? percent(p.success_rate)
            : `${percent(p.success_rate)} ${unknown("— too few runs to mean much")}`) +
        (p.failures ? kv("Failures", p.failures) : "") +
        (p.refusals ? kv("Refused", p.refusals) : "") +
        kv("Average confidence", p.avg_confidence === null ? unknown("not recorded")
            : p.avg_confidence.toFixed(2)) +
        kv("Average time", p.avg_latency_ms === null ? unknown("not recorded")
            : `${(p.avg_latency_ms / 1000).toFixed(1)}s when it succeeds`)
      : kv("Runs", unknown("has not run in the last 30 days"))) +
    `<p class="note">Correction rate and quality score are not shown because
     nothing measures them yet. A blank is safer than an estimate.</p>`;

  html += `<div class="sect">What it costs</div>` +
    kv("Billed today", costInr(e.spend_today_inr)) +
    kv("Billed, 30 days", costInr(e.spend_30d_inr)) +
    kv("At paid rates, 30 days", costInr(e.shadow_30d_inr)) +
    kv("Per task", e.spend_per_task_inr === null
        ? unknown("no finished tasks yet")
        : `${costInr(e.spend_per_task_inr)} billed · ${costInr(e.shadow_per_task_inr)} at paid rates`);

  html += `<div class="sect">Activity</div>`;
  if (a.current.length) {
    html += a.current.map((t) =>
      kv("Working on", esc(t.objective)) +
      kv("Task", esc(t.task_id)) +
      (t.workflow_objective ? kv("Part of", esc(t.workflow_objective)) : "") +
      kv("Started", t.started_at ? new Date(t.started_at).toLocaleTimeString() : "—") +
      kv("Model", esc(t.model || "choosing")) +
      kv("Budget", t.budget_inr === null ? "workflow default" : costInr(t.budget_inr))
    ).join("");
  } else {
    html += kv("Right now", "idle");
  }
  if (a.queued.length) html += kv("Queued", a.queued.length);
  if (a.recent.length) {
    html += `<div class="sect">Recently</div>` + a.recent.map((t) =>
      `<div class="perm">${t.status === "completed" ? "✓" : "✕"} ${esc(t.objective)}` +
      `<span class="unknown"> — ${esc(t.status)}${
        t.finished_at ? ", " + new Date(t.finished_at).toLocaleString() : ""}</span></div>`
    ).join("");
  }
  if (a.last_failure) {
    html += kv("Last failure", esc(a.last_failure.failure_reason || "—"));
  }

  html += `<p class="note">No agent is created from this page. A new
   capability is written, reviewed, registered as experimental and only
   then activated — agents are not made in production with a button.</p>`;
  return html;
}
