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
    note.textContent = detail ? `Listening — ${detail}. Tap the mic to stop.` : "Listening…";
    setBusy(false);
    return;
  }
  if (state === "speaking") { setBusy(true); return; }
  if (state === "off") {
    btn.classList.remove("rec");
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
      addMsg("jarvis", data.reply,
        `${data.provider} · ${data.recalled_turns} turns, ${data.recalled_facts} facts · ` +
        `${(data.total_ms / 1000).toFixed(1)}s`);
      speak(data.reply);
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
const views = {
  home: () => { $("settingsView").classList.add("hidden"); document.querySelector(".grid").classList.remove("hidden"); },
  settings: () => { document.querySelector(".grid").classList.add("hidden"); $("settingsView").classList.remove("hidden"); },
};
$("nav").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-view]");
  if (!btn) return;
  [...$("nav").querySelectorAll("button[data-view]")].forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");
  views[btn.dataset.view]();
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
start();
