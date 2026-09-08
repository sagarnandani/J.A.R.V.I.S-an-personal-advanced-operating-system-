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
const api = (path, opts = {}) =>
  fetch(path, {
    ...opts,
    credentials: "same-origin",
    headers: { ...(opts.headers || {}), "Content-Type": "application/json" },
  });

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

function loadVoices() { if (canSpeak) voices = speechSynthesis.getVoices() || []; }
if (canSpeak) {
  loadVoices();
  speechSynthesis.addEventListener("voiceschanged", loadVoices);
}

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

function pickVoice(lang) {
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
  // JARVIS is Paul Bettany, after all.
  return (
    voices.find((v) => /en-GB/i.test(v.lang) && /(daniel|arthur|male)/i.test(v.name)) ||
    voices.find((v) => /en-GB/i.test(v.lang)) ||
    voices.find((v) => /^en/i.test(v.lang)) ||
    null
  );
}

function speak(text) {
  if (!speakOn || !canSpeak || !text) return;
  try {
    speechSynthesis.cancel();
    loadVoices();
    const lang = langOf(text);
    const u = new SpeechSynthesisUtterance(text.slice(0, 800));
    const v = pickVoice(lang);
    // Setting .voice can throw if the browser hands back something it
    // will not accept. Losing the preferred accent is a small loss;
    // losing the whole utterance because of it is not, so the language
    // is set either way and JARVIS still speaks.
    if (v) {
      try { u.voice = v; } catch (e) { /* fall back to lang alone */ }
      u.lang = v.lang;
    } else if (lang) {
      u.lang = lang;
    }
    u.rate = 1.02;
    u.pitch = 0.92;
    // The waveform moves while JARVIS is actually speaking, so silence
    // caused by a missing voice looks different from silence caused by
    // nothing being said.
    u.onstart = () => setBusy(true);
    u.onend = () => setBusy(false);
    u.onerror = () => { setBusy(false); voiceProblem("The browser refused to play the voice."); };
    speechSynthesis.speak(u);

    if (lang && !v) {
      voiceProblem(
        `No ${lang} voice is installed on this device, so JARVIS cannot ` +
        `read that reply aloud. On iPad: Settings → Accessibility → ` +
        `Spoken Content → Voices.`
      );
    }
  } catch (e) {
    voiceProblem(`Speech failed: ${e.message}`);
  }
}

// Voice failing must say so. It failed silently for every reply until now,
// which is indistinguishable from the feature not existing.
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

  // activity
  const feed = $("feed");
  feed.innerHTML = "";
  if (!d.activity.length) {
    feed.innerHTML = "<li class='empty'>No activity recorded yet.</li>";
  } else {
    for (const a of d.activity) {
      const when = new Date(a.at);
      const li = document.createElement("li");
      const risk = a.category === "high_risk" ? "high" : a.category === "medium_risk" ? "med" : "";
      li.innerHTML =
        `<span class="t">${pad(when.getHours())}:${pad(when.getMinutes())}</span>` +
        `<span class="dot ${risk}"></span>` +
        `<span class="d">${esc(describe(a))}` +
        (a.outcome ? `<small>${esc(a.outcome)}</small>` : "") + `</span>`;
      feed.appendChild(li);
    }
  }
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

function describe(a) {
  const map = {
    llm_message_exchange: "Message answered",
    memory_learn: "Learned something new",
    memory_forget: "Forgot a memory",
    memory_restore: "Restored a memory",
    memory_delete: "Deleted a memory",
    memory_forget_all: "Forgot everything",
    memory_purge: "Erased forgotten memories",
    sign_in: "Signed in",
    sign_in_refused_not_owner: "Refused a sign-in",
  };
  if (map[a.action]) return map[a.action];
  if (a.action.startsWith("emergency_stop")) return "Emergency stop changed";
  return a.action.replace(/_/g, " ");
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
  if (!text) return;
  input.value = "";
  addMsg("you", text);
  setBusy(true);
  $("convoNote").textContent = "Thinking… the first message after a break can take a minute while the server wakes.";

  try {
    const res = await api("/v1/message", { method: "POST", body: JSON.stringify({ text }) });
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
const VIEWS = { home: null, tasks: "tasksView", settings: "settingsView" };
const homeGrid = () => document.querySelector(".grid");

function showView(name) {
  homeGrid().classList.toggle("hidden", name !== "home");
  for (const [view, id] of Object.entries(VIEWS)) {
    if (id) $(id).classList.toggle("hidden", view !== name);
  }
  if (name === "tasks") { loadWorkflows(); loadSchedules(); }
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
    speak("Voice enabled.");
  } else {
    try { speechSynthesis.cancel(); } catch (err) {}
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

/* ------------------------------------------------------------- start-up */
async function start() {
  const ok = await refresh();
  if (ok) {
    gate.classList.add("hidden");
    app.classList.remove("hidden");
    setupMic();
    // Keep the numbers honest without hammering a sleepy free-tier server.
    setInterval(refresh, 60000);
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
  box.innerHTML =
    `<div class="what">I can look this up properly: ${esc(offer.objective)}</div>` +
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
  go.onclick = () => runOffer(box, offer.objective);
  return box;
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
      "Scheduled. It plans and runs on its own — you approve nothing at the time, " +
      "so it stops at 60% of your monthly budget and twice a day at most.";
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

start();
