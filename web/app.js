/* Show Me phone page. Plain JS, no framework.
   Flow: hold mic -> transcribe -> capture frame -> POST /runs -> draw outline + speak step 1
         tap "Done, check it" -> capture frame -> POST /runs/{id}/advance -> speak feedback / next step
         needs_input -> message + "I did it, continue" (POST /runs/{id}/confirm, logged as human-confirmed)
   The access key comes once from the address (#k=...) and lives in localStorage. Never in this file. */

const BACKEND = "https://nirup2721--show-me-web.us-east.modal.run";
const FRAME_LONG_SIDE = 768;

const $ = (id) => document.getElementById(id);
const cam = $("cam"), still = $("still"), overlay = $("overlay"), statusEl = $("status");
const mic = $("mic"), micLabel = $("micLabel"), nextBtn = $("next"), confirmBtn = $("confirm"), restartBtn = $("restart");
const actions = $("actions"), typed = $("typed"), questionInput = $("question");

let run = null;          // the current run record from the server
let busy = false;
let recorder = null, chunks = [];
let frameSource = "camera";   // "camera" | "still"

// ---------- access key ----------
function getKey() {
  const m = (location.hash + location.search).match(/[#&?]k=([^&]+)/);
  if (m) { localStorage.setItem("showme_key", decodeURIComponent(m[1])); history.replaceState(null, "", location.pathname); }
  let k = localStorage.getItem("showme_key");
  if (!k) {
    try { k = prompt("Enter the Show Me access key"); } catch (e) { k = ""; }
    if (k) localStorage.setItem("showme_key", k.trim());
    else setStatus("No access key. Open the link you were given (it ends with #k=...).", "warn");
  }
  return k || "";
}
let KEY = "";

// ---------- status + speech ----------
function setStatus(text, cls = "") { statusEl.textContent = text; statusEl.className = cls; }
function speak(text) {
  if (!("speechSynthesis" in window) || !text) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "en-US"; u.rate = 1.0;
  speechSynthesis.speak(u);
}

// ---------- camera ----------
async function startCamera() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 } }, audio: false });
    cam.srcObject = stream;
    frameSource = "camera";
  } catch (e) {
    setStatus("No camera here. Use the photo button to pick a picture, or type a question.", "warn");
    frameSource = "still";
  }
}

// Dev helpers (desktop testing only, same-origin): #img=/path.jpg loads a picture instead of the camera;
// #devkey=1 reads the access key from /dev_key.txt served by a local dev server (never present in production).
function loadDevImage() {
  const m = location.hash.match(/img=([^&]+)/);
  if (!m) return false;
  showStill(decodeURIComponent(m[1]));
  return true;
}
async function loadDevKey() {
  if (!/devkey=1/.test(location.hash) || !/^(localhost|127\.0\.0\.1)$/.test(location.hostname)) return;
  try { const t = (await (await fetch("/dev_key.txt")).text()).trim(); if (t) localStorage.setItem("showme_key", t); } catch (e) {}
}
function showStill(src) {
  still.src = src; still.hidden = false; cam.hidden = true; frameSource = "still";
}

$("photo").addEventListener("click", () => $("file").click());
$("file").addEventListener("change", (e) => {
  const f = e.target.files[0]; if (!f) return;
  showStill(URL.createObjectURL(f));
  setStatus("Photo loaded. Hold the mic or type your question.");
});

// Capture the current view as a JPEG no larger than FRAME_LONG_SIDE. Returns {blob, w, h}.
function captureFrame() {
  return new Promise((resolve, reject) => {
    const src = frameSource === "camera" ? cam : still;
    const sw = frameSource === "camera" ? cam.videoWidth : still.naturalWidth;
    const sh = frameSource === "camera" ? cam.videoHeight : still.naturalHeight;
    if (!sw || !sh) return reject(new Error("no picture yet"));
    const scale = Math.min(1, FRAME_LONG_SIDE / Math.max(sw, sh));
    const c = document.createElement("canvas");
    c.width = Math.round(sw * scale); c.height = Math.round(sh * scale);
    c.getContext("2d").drawImage(src, 0, 0, c.width, c.height);
    c.toBlob((b) => b ? resolve({ blob: b, w: c.width, h: c.height }) : reject(new Error("capture failed")), "image/jpeg", 0.82);
  });
}

// ---------- overlay ----------
// The server's coordinates are in the frame it analysed (run.frame_size). Map them onto the displayed picture.
function displayedRect() {
  const el = frameSource === "camera" ? cam : still;
  const sw = frameSource === "camera" ? cam.videoWidth : still.naturalWidth;
  const sh = frameSource === "camera" ? cam.videoHeight : still.naturalHeight;
  const W = overlay.clientWidth, H = overlay.clientHeight;
  const s = Math.min(W / sw, H / sh);
  return { x: (W - sw * s) / 2, y: (H - sh * s) / 2, w: sw * s, h: sh * s };
}
function drawOutline() {
  const dpr = window.devicePixelRatio || 1;
  overlay.width = overlay.clientWidth * dpr; overlay.height = overlay.clientHeight * dpr;
  const ctx = overlay.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, overlay.clientWidth, overlay.clientHeight);
  const o = run && run.outline; if (!o || !run.frame_size || o.kind === "too_far") return;
  const [fw, fh] = run.frame_size;
  const r = displayedRect();
  const k = r.w / fw;                      // frame px -> screen px
  ctx.save();
  ctx.translate(r.x, r.y); ctx.scale(k, k);
  ctx.lineWidth = 3 / k; ctx.strokeStyle = "#35d07f"; ctx.fillStyle = "rgba(53,208,127,0.28)";
  if (o.kind === "mask" && o.svg_path) {
    const [bl, bt, br, bb] = o.bounds;
    ctx.save();
    ctx.translate(bl, bt); ctx.scale((br - bl) / o.mask_w, (bb - bt) / o.mask_h);
    const p = new Path2D(o.svg_path);
    ctx.fill(p);
    ctx.lineWidth = 3 / k / ((br - bl) / o.mask_w);
    ctx.stroke(p);
    ctx.restore();
  } else if (o.box) {
    const [x1, y1, x2, y2] = o.box;
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
  }
  // label
  const [x1, y1] = o.box || o.bounds;
  ctx.font = `${16 / k}px system-ui`; ctx.fillStyle = "#35d07f";
  ctx.fillText(run.part_name || "", x1, Math.max(14 / k, y1 - 6 / k));
  ctx.restore();
}
window.addEventListener("resize", drawOutline);
cam.addEventListener("loadedmetadata", drawOutline);

// ---------- server ----------
async function api(path, form) {
  const r = await fetch(BACKEND + path, { method: form ? "POST" : "GET", body: form, headers: { "X-Show-Me-Key": KEY } });
  if (r.status === 401) throw new Error("Access key wrong. Reload the page with the link you were given.");
  if (r.status === 503) throw new Error("Spending limit reached. Paid calls are off.");
  if (!r.ok) throw new Error(`Server error ${r.status}`);
  return r.json();
}

function render(r) {
  run = r;
  $("run").textContent = r.id ? `run ${r.id}` : "";
  $("cost").textContent = r.cost_usd ? `· $${r.cost_usd.toFixed(3)}` : "";
  const cls = { guiding: "", needs_input: "warn", done: "ok", failed: "bad" }[r.status] || "";
  setStatus(r.message || "", cls);
  speak(r.message);
  drawOutline();
  actions.hidden = false;
  nextBtn.hidden = !(r.status === "guiding" || r.status === "needs_input");
  nextBtn.textContent = r.status === "needs_input" ? "Check again" : "Done, check it";
  confirmBtn.hidden = r.status !== "needs_input" || !(r.steps && r.steps.length) || run.outline?.kind === "too_far";
  restartBtn.hidden = !(r.status === "done" || r.status === "failed");
}

async function startRun(question) {
  if (busy || !question) return;
  busy = true; setStatus("Looking…", "busy");
  try {
    const f = await captureFrame();
    const form = new FormData();
    form.append("question", question);
    form.append("frame", f.blob, "frame.jpg");
    render(await api("/runs", form));
  } catch (e) { setStatus(e.message, "bad"); }
  busy = false;
}

async function advance() {
  if (busy || !run) return;
  busy = true; setStatus("Checking…", "busy"); nextBtn.disabled = true;
  try {
    const f = await captureFrame();
    const form = new FormData(); form.append("frame", f.blob, "frame.jpg");
    render(await api(`/runs/${run.id}/advance`, form));
  } catch (e) { setStatus(e.message, "bad"); }
  nextBtn.disabled = false; busy = false;
}

async function confirmHuman() {
  if (busy || !run) return;
  busy = true;
  try { render(await api(`/runs/${run.id}/confirm`, new FormData())); } catch (e) { setStatus(e.message, "bad"); }
  busy = false;
}

function restart() {
  run = null; actions.hidden = true; drawOutline(); speechSynthesis.cancel();
  setStatus("Point the camera at the machine, then hold the mic and ask.");
}

nextBtn.addEventListener("click", advance);
confirmBtn.addEventListener("click", confirmHuman);
restartBtn.addEventListener("click", restart);

// ---------- hold to talk ----------
async function startRecording() {
  if (busy || recorder) return;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"].find((m) => MediaRecorder.isTypeSupported(m)) || "";
    recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    chunks = [];
    recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      const type = recorder.mimeType || "audio/webm";
      recorder = null; mic.classList.remove("rec"); micLabel.textContent = "Hold to talk";
      const blob = new Blob(chunks, { type });
      if (blob.size < 2000) { setStatus("Too short. Hold the button while you speak.", "warn"); return; }
      busy = true; setStatus("Listening…", "busy");
      try {
        const form = new FormData();
        form.append("audio", blob, "q." + (type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm"));
        const t = await api("/transcribe", form);
        busy = false;
        if (!t.text) { setStatus("I didn't catch that. Try again.", "warn"); return; }
        setStatus(`"${t.text}"`);
        await startRun(t.text);
      } catch (e) { busy = false; setStatus(e.message, "bad"); }
    };
    recorder.start();
    mic.classList.add("rec"); micLabel.textContent = "Listening…";
    speechSynthesis.cancel();
  } catch (e) {
    setStatus("Microphone not available. Type your question instead.", "warn");
    typed.hidden = false;
  }
}
function stopRecording() { if (recorder && recorder.state === "recording") recorder.stop(); }
mic.addEventListener("pointerdown", (e) => { e.preventDefault(); startRecording(); });
mic.addEventListener("pointerup", stopRecording);
mic.addEventListener("pointercancel", stopRecording);
mic.addEventListener("pointerleave", stopRecording);
mic.addEventListener("contextmenu", (e) => e.preventDefault());

// ---------- typed question ----------
$("type").addEventListener("click", () => { typed.hidden = !typed.hidden; if (!typed.hidden) questionInput.focus(); });
$("ask").addEventListener("click", () => { const q = questionInput.value.trim(); if (q) startRun(q); });
questionInput.addEventListener("keydown", (e) => { if (e.key === "Enter") $("ask").click(); });

// ---------- boot ----------
(async () => {
  await loadDevKey();
  KEY = getKey();
  if (!loadDevImage()) startCamera();
})();
