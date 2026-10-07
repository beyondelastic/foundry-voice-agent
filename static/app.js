// Browser side of the lab bench voice assistant:
// - streams the microphone (plus what the speakers play, for echo cancellation) to app.py
// - plays Vita's voice and the music, and stops Vita when you interrupt
// - draws the simulated lab from the state that app.py pushes after each tool call

const SAMPLE_RATE = 24000;
const COLORS = { white: "#ffffff", warm: "#ffc977", blue: "#6fb7ff", green: "#6dffa8", red: "#ff6b6b" };
const BLINDS = { open: 0.06, half: 0.5, closed: 1 };
const DAYLIGHT = { open: 1, half: 0.5, closed: 0 };
const MUSIC_LEVEL = 0.5, MUSIC_DUCKED = 0.12;  // music volume, normal and while Vita talks
// Fallback music, used when static/music/<playlist>.mp3 doesn't exist
const SYNTH = {
  calm: { notes: [261.63, 293.66, 329.63, 392, 440], beat: 0.9, wave: "sine" },
  focus: { notes: [220, 261.63, 293.66, 329.63, 392, 440], beat: 0.45, wave: "triangle" },
  upbeat: { notes: [293.66, 369.99, 440, 493.88, 587.33], beat: 0.25, wave: "triangle", kick: true },
};

const $ = (id) => document.getElementById(id);
let ws, ctx, mic, micSource, speakers, voiceBus, musicBus, micMeter, voiceMeter;
let playTime = 0, playing = [], muted = false, music = null, state = null;

$("start").onclick = () => (ws ? stopSession() : startSession());
fetch("/api/state").then((r) => r.json()).then((s) => render((state = s)));
$("orb").classList.add("off");

// ---------- Session ----------
async function startSession() {
  ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
  await ctx.audioWorklet.addModule("/static/mic-worklet.js");
  mic = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });

  // Everything we play goes through "speakers", which is also the echo reference.
  speakers = ctx.createGain();
  speakers.connect(ctx.destination);
  voiceBus = ctx.createGain();
  musicBus = ctx.createGain();
  musicBus.gain.value = MUSIC_LEVEL;
  voiceBus.connect(speakers);
  musicBus.connect(speakers);
  micSource = ctx.createMediaStreamSource(mic);
  micMeter = meter(micSource);
  voiceMeter = meter(voiceBus);

  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.binaryType = "arraybuffer";
  ws.onmessage = (e) => (typeof e.data === "string" ? onEvent(JSON.parse(e.data)) : playAudio(e.data));
  ws.onclose = () => stopSession();

  $("start").textContent = "⏹️ End session";
  $("start").classList.add("active");
  $("orb").classList.remove("off");
  setStatus("Connecting to the voice agent...");
  render(state);
  animateOrb();
}

function startCapture(channels) {
  // Input 0: microphone. Input 1: speaker output (only used in stereo mode).
  const capture = new AudioWorkletNode(ctx, "mic-capture", {
    numberOfInputs: 2, numberOfOutputs: 0, channelCount: 1, channelCountMode: "explicit",
    processorOptions: { channels },
  });
  capture.port.onmessage = (e) => ws?.readyState === WebSocket.OPEN && ws.send(e.data);
  micSource.connect(capture, 0, 0);
  speakers.connect(capture, 0, 1);
}

function stopSession() {
  if (!ws) return;
  const socket = ws;
  ws = null;
  socket.close();
  stopPlayback();
  mic?.getTracks().forEach((t) => t.stop());
  stopMusic();
  ctx?.close();
  ctx = null;
  playing = [];
  $("start").textContent = "🎙️ Start session";
  $("start").classList.remove("active");
  $("orb").classList.add("off");
}

function onEvent(msg) {
  switch (msg.type) {
    case "config": startCapture(msg.channels); break;
    case "state": render((state = msg.state)); break;
    case "transcript": addLine($("transcript"), msg.text, `msg ${msg.role}`); break;
    case "tool": addLine($("tools"), `${msg.name}(${JSON.stringify(msg.args)}) → ${msg.result.ok ? "ok" : msg.result.message}`); break;
    case "interrupt": stopPlayback(); muted = true; break;  // you started talking
    case "response_started": muted = false; break;
    case "status": setStatus(msg.text); break;
    case "error": setStatus(msg.text, true); break;
  }
}

// ---------- Vita's voice ----------
function playAudio(data) {
  if (muted || !ctx) return;
  const pcm = new Int16Array(data);
  const buffer = ctx.createBuffer(1, pcm.length, SAMPLE_RATE);
  const samples = buffer.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) samples[i] = pcm[i] / 0x8000;

  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(voiceBus);
  playTime = Math.max(playTime, ctx.currentTime);
  source.start(playTime);
  playTime += buffer.duration;
  playing.push(source);
  source.onended = () => { playing = playing.filter((s) => s !== source); duck(); };
  duck();
}

function stopPlayback() {
  playing.forEach((s) => s.stop());
  playing = [];
  playTime = 0;
  duck();
}

// Turn the music down while Vita is talking
function duck() {
  if (ctx) musicBus.gain.setTargetAtTime(playing.length ? MUSIC_DUCKED : MUSIC_LEVEL, ctx.currentTime, 0.15);
}

// ---------- Music: your own file, or a little generative synth ----------
function startMusic(name) {
  if (!ctx || music?.name === name) return;
  stopMusic();
  const el = new Audio(`/static/music/${name}.mp3`);
  el.loop = true;
  const node = ctx.createMediaElementSource(el);
  node.connect(musicBus);
  music = { name, source: "file", stop: () => { el.pause(); node.disconnect(); } };
  el.play().catch(() => {
    if (music?.name !== name) return;
    music.stop();
    music = { name, source: "synth", stop: synth(SYNTH[name]) };
    render(state);
  });
}

function stopMusic() {
  music?.stop();
  music = null;
}

function synth(p) {
  const echo = ctx.createDelay();
  const feedback = ctx.createGain();
  echo.delayTime.value = p.beat * 0.75;
  feedback.gain.value = 0.35;
  echo.connect(feedback).connect(echo);
  echo.connect(musicBus);
  let step = 0;
  const tick = () => {
    const t = ctx.currentTime;
    const osc = ctx.createOscillator();
    const env = ctx.createGain();
    osc.type = p.wave;
    osc.frequency.value = p.notes[Math.floor(Math.random() * p.notes.length)] * (step % 8 === 0 ? 0.5 : 1);
    env.gain.setValueAtTime(0.0001, t);
    env.gain.exponentialRampToValueAtTime(0.12, t + 0.02);
    env.gain.exponentialRampToValueAtTime(0.0001, t + p.beat * 3);
    osc.connect(env).connect(musicBus);
    env.connect(echo);
    osc.start(t);
    osc.stop(t + p.beat * 3);
    if (p.kick && step % 4 === 0) kick(t);
    step++;
  };
  const timer = setInterval(tick, p.beat * 1000);
  return () => { clearInterval(timer); echo.disconnect(); feedback.disconnect(); };
}

function kick(t) {
  const osc = ctx.createOscillator();
  const env = ctx.createGain();
  osc.frequency.setValueAtTime(120, t);
  osc.frequency.exponentialRampToValueAtTime(45, t + 0.15);
  env.gain.setValueAtTime(0.25, t);
  env.gain.exponentialRampToValueAtTime(0.0001, t + 0.25);
  osc.connect(env).connect(musicBus);
  osc.start(t);
  osc.stop(t + 0.3);
}

// ---------- Voice orb ----------
function meter(node) {
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 512;
  node.connect(analyser);
  const data = new Float32Array(analyser.fftSize);
  return () => {
    analyser.getFloatTimeDomainData(data);
    return Math.sqrt(data.reduce((sum, v) => sum + v * v, 0) / data.length);
  };
}

function animateOrb() {
  if (!ctx) return document.documentElement.style.setProperty("--level", 0);
  const speaking = playing.length > 0;
  const level = Math.min(1, (speaking ? voiceMeter() : micMeter()) * 6);
  $("orb").classList.toggle("speaking", speaking);
  document.documentElement.style.setProperty("--level", level.toFixed(2));
  requestAnimationFrame(animateOrb);
}

// ---------- Render the room ----------
let shotCount = null;  // null until the first render, so the page load doesn't flash

function render(s) {
  if (!s) return;
  const root = document.documentElement.style;
  const brightness = s.lights.power ? s.lights.brightness / 100 : 0;
  root.setProperty("--light", COLORS[s.lights.color] || "#ffffff");
  root.setProperty("--brightness", brightness);
  root.setProperty("--dark", (0.78 * (1 - brightness) * (1 - 0.25 * DAYLIGHT[s.blinds])).toFixed(2));
  root.setProperty("--blinds", BLINDS[s.blinds]);
  root.setProperty("--daylight", DAYLIGHT[s.blinds]);

  $("scene").textContent = cap(s.scene ? s.scene.replaceAll("_", " ") : "none");
  $("door-sign").classList.toggle("on", ["cell_culture", "microscopy"].includes(s.scene));
  $("light-label").textContent = cap(s.lights.power ? `${s.lights.color} · ${s.lights.brightness}%` : "off");
  $("blinds-label").textContent = cap(s.blinds);

  const source = music?.source === "synth" ? " (synth)" : "";
  $("music-label").textContent = cap(`${s.music.playlist} · ${s.music.playing ? "playing" + source : "paused"}`);
  $("music").classList.toggle("playing", s.music.playing);
  document.querySelector(".waves").classList.toggle("on", s.music.playing);
  s.music.playing ? startMusic(s.music.playlist) : stopMusic();

  const camera = s.camera;
  $("rec").classList.toggle("on", camera.recording);
  $("rec-led").classList.toggle("on", camera.recording);
  $("camera-label").textContent = camera.recording ? "● Recording" : `Standby · ${camera.snapshots} ${camera.snapshots === 1 ? "snap" : "snaps"}`;
  if (shotCount !== null && camera.snapshots > shotCount) {
    $("flash").classList.remove("on");
    void $("flash").getBoundingClientRect();  // restart the flash animation
    $("flash").classList.add("on");
  }
  shotCount = camera.snapshots;
  $("shots").innerHTML = Array.from({ length: Math.min(shotCount, 8) }, (_, i) =>
    `<rect class="shot" x="${620 + i * 34}" y="378" width="28" height="22" rx="3" fill="url(#thumb)"/>`).join("");
}

// ---------- Clock, recording timer, and (cosmetic) vitals ----------
setInterval(() => {
  const now = new Date();
  const hand = (id, deg) => $(id).setAttribute("transform", `rotate(${deg} 120 132)`);
  hand("hour", (now.getHours() % 12) * 30 + now.getMinutes() / 2);
  hand("minute", now.getMinutes() * 6);
  hand("second", now.getSeconds() * 6);

  const since = state?.camera.recording_since;
  if (since) {
    const secs = Math.max(0, Math.floor(Date.now() / 1000 - since));
    $("rec-time").textContent = `${String(Math.floor(secs / 60)).padStart(2, "0")}:${String(secs % 60).padStart(2, "0")}`;
  }
  if (now.getSeconds() % 3 === 0) {
    $("temp").textContent = (36.9 + Math.random() * 0.2).toFixed(1);
    $("co2").textContent = (4.9 + Math.random() * 0.2).toFixed(1);
  }
}, 1000);

// ---------- Helpers ----------
const cap = (text) => text.charAt(0).toUpperCase() + text.slice(1);

function addLine(container, text, className = "") {
  const div = document.createElement("div");
  div.className = className;
  div.textContent = text;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function setStatus(text, isError = false) {
  $("status").textContent = text;
  $("status").classList.toggle("error", isError);
}
