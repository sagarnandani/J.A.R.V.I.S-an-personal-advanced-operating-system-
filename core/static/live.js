/* Live voice: talking to JARVIS, rather than typing at it.
 *
 * The browser's speech engine reads text aloud. This is a different
 * thing: Gemini generates the speech itself, which is why it handles
 * Kannada, Hindi, Marathi and English -- including mixed inside one
 * sentence, which dictation-then-text never manages.
 *
 * Two audio paths, and they use different sample rates, which is not a
 * detail: the wrong rate produces silence or chipmunks, never an error.
 *
 *   microphone -> 16kHz mono PCM16 -> base64 -> server -> Gemini
 *   Gemini -> server -> base64 -> 24kHz PCM16 -> speakers
 *
 * Playback is scheduled rather than fired: each chunk is booked to start
 * exactly where the previous one ends. Playing them as they arrive leaves
 * audible gaps, because network jitter is larger than the chunks are.
 */
const LIVE = {
  ws: null,
  ctx: null,
  micStream: null,
  node: null,
  playAt: 0,
  sources: [],
  active: false,
  inRate: 16000,
  outRate: 24000,
};

function liveState(state, detail) {
  document.dispatchEvent(
    new CustomEvent("jarvis:live", { detail: { state, detail } })
  );
}

// Float samples from the microphone, resampled to what Gemini expects and
// converted to signed 16-bit. Straight-line resampling is enough here:
// speech at 16kHz has nothing near the Nyquist limit for the browser's
// usual 44.1/48kHz capture rate.
function toPcm16(float32, fromRate, toRate) {
  const ratio = fromRate / toRate;
  const outLength = Math.floor(float32.length / ratio);
  const out = new Int16Array(outLength);
  for (let i = 0; i < outLength; i++) {
    const s = Math.max(-1, Math.min(1, float32[Math.floor(i * ratio)]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

function b64(bytes) {
  let s = "";
  const chunk = 0x8000; // one call per 32k samples; the whole buffer overflows the arg list
  for (let i = 0; i < bytes.length; i += chunk) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(s);
}

function unb64(str) {
  const bin = atob(str);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function play(bytes) {
  if (!LIVE.ctx) return;
  const pcm = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 2);
  const buf = LIVE.ctx.createBuffer(1, pcm.length, LIVE.outRate);
  const channel = buf.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 32768;

  const src = LIVE.ctx.createBufferSource();
  src.buffer = buf;
  src.connect(LIVE.ctx.destination);

  // Book this chunk where the last one ends. A small cushion absorbs
  // jitter without being audible as delay.
  const now = LIVE.ctx.currentTime;
  if (LIVE.playAt < now) LIVE.playAt = now + 0.06;
  src.start(LIVE.playAt);
  LIVE.playAt += buf.duration;

  LIVE.sources.push(src);
  src.onended = () => {
    LIVE.sources = LIVE.sources.filter((s) => s !== src);
    if (!LIVE.sources.length) liveState("listening");
  };
  liveState("speaking");
}

// Interruption: when you start talking over JARVIS, the model stops
// generating and everything already queued must be dropped. Letting it
// finish means it talks over your interruption, which is worse than not
// being interruptible at all.
function stopPlayback() {
  for (const s of LIVE.sources) { try { s.stop(); } catch (e) {} }
  LIVE.sources = [];
  LIVE.playAt = 0;
}

async function startLive() {
  if (LIVE.active) return;
  liveState("connecting");

  try {
    LIVE.micStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
  } catch (e) {
    liveState("error", "Microphone permission was refused, so JARVIS cannot hear you.");
    return;
  }

  // One AudioContext for both directions. Created inside the tap that
  // started this, because iOS will not let audio begin otherwise.
  LIVE.ctx = new (window.AudioContext || window.webkitAudioContext)();
  if (LIVE.ctx.state === "suspended") await LIVE.ctx.resume();

  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  LIVE.ws = new WebSocket(`${proto}//${location.host}/v1/live`);

  LIVE.ws.onopen = () => { /* the server speaks first, with 'ready' */ };

  LIVE.ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    switch (msg.type) {
      case "ready":
        LIVE.inRate = msg.input_rate || 16000;
        LIVE.outRate = msg.output_rate || 24000;
        LIVE.active = true;
        beginCapture();
        liveState("listening", `${msg.voice} · ${msg.model}`);
        break;
      case "audio":
        play(unb64(msg.data));
        break;
      case "you":
        liveState("heard", msg.text);
        break;
      case "jarvis":
        liveState("said", msg.text);
        break;
      case "interrupted":
        stopPlayback();
        liveState("listening");
        break;
      case "turn_complete":
        liveState("turn");
        break;
      case "error":
        liveState("error", msg.message);
        stopLive();
        break;
    }
  };

  LIVE.ws.onerror = () => liveState("error", "The live connection failed.");
  LIVE.ws.onclose = () => { if (LIVE.active) stopLive(); };
}

function beginCapture() {
  const source = LIVE.ctx.createMediaStreamSource(LIVE.micStream);
  // ScriptProcessor is deprecated in favour of AudioWorklet, and is used
  // anyway: it needs no separate module file, works in every browser that
  // matters here including Safari, and the deprecation has no removal
  // date. An AudioWorklet would be tidier and is a change for later.
  const node = LIVE.ctx.createScriptProcessor(4096, 1, 1);
  node.onaudioprocess = (e) => {
    if (!LIVE.ws || LIVE.ws.readyState !== WebSocket.OPEN) return;
    const pcm = toPcm16(e.inputBuffer.getChannelData(0), LIVE.ctx.sampleRate, LIVE.inRate);
    LIVE.ws.send(JSON.stringify({
      type: "audio",
      data: b64(new Uint8Array(pcm.buffer)),
    }));
  };
  source.connect(node);
  // ScriptProcessor only runs while connected to a destination. Routed
  // through a silent gain node so the microphone is not echoed to the
  // speakers, which would be a feedback loop.
  const mute = LIVE.ctx.createGain();
  mute.gain.value = 0;
  node.connect(mute);
  mute.connect(LIVE.ctx.destination);
  LIVE.node = node;
}

function stopLive() {
  LIVE.active = false;
  stopPlayback();
  try { LIVE.ws && LIVE.ws.readyState === WebSocket.OPEN && LIVE.ws.send(JSON.stringify({ type: "end" })); } catch (e) {}
  try { LIVE.ws && LIVE.ws.close(); } catch (e) {}
  try { LIVE.node && LIVE.node.disconnect(); } catch (e) {}
  try { LIVE.micStream && LIVE.micStream.getTracks().forEach((t) => t.stop()); } catch (e) {}
  try { LIVE.ctx && LIVE.ctx.close(); } catch (e) {}
  LIVE.ws = LIVE.node = LIVE.micStream = LIVE.ctx = null;
  liveState("off");
}

function liveActive() { return LIVE.active; }

export { startLive, stopLive, liveActive };
