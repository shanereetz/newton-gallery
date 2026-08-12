const examples = [
  { id: "basic_shapes", name: "Basic Shapes", category: "Basics", difficulty: "Beginner", image: "./public/examples/basic-shapes.jpg", description: "A rigid-body scene with spheres, capsules, boxes, a mesh, contacts, and gravity.", tags: ["Rigid bodies", "Contacts", "Starter"] },
  { id: "robot_g1", name: "G1 Humanoid", category: "Robots", difficulty: "Intermediate", image: "./public/examples/robot-g1.jpg", description: "Explore articulated humanoid dynamics with a Unitree G1 model and Newton's MuJoCo Warp backend.", tags: ["Humanoid", "MuJoCo Warp", "Articulation"] },
  { id: "robot_anymal_c_walk", name: "ANYmal C Walk", category: "Robots", difficulty: "Intermediate", image: "./public/examples/anymal-walk.jpg", description: "Run a quadruped locomotion policy and inspect the synchronized gait in real time.", tags: ["Locomotion", "Policy", "Quadruped"] },
  { id: "cloth_franka", name: "Franka Cloth", category: "Cloth", difficulty: "Advanced", image: "./public/examples/cloth-franka.jpg", description: "Watch a Franka arm interact with deformable cloth in a contact-rich manipulation scene.", tags: ["Deformables", "Franka", "Contact"] },
  { id: "mpm_twoway_coupling", name: "Two-way Coupling", category: "MPM", difficulty: "Advanced", image: "./public/examples/mpm-coupling.jpg", description: "Inspect two-way coupling between articulated rigid bodies and a material point system.", tags: ["MPM", "Coupling", "Multi-physics"] },
  { id: "ik_franka", name: "Franka IK", category: "IK", difficulty: "Intermediate", image: "./public/examples/ik-franka.jpg", description: "Drive a Franka manipulator toward target poses with Newton's inverse kinematics tools.", tags: ["Kinematics", "Franka", "Control"] },
  { id: "diffsim_drone", name: "Differentiable Drone", category: "DiffSim", difficulty: "Advanced", image: "./public/examples/diffsim-drone.jpg", description: "Visualize a drone trajectory through a compact differentiable simulation example.", tags: ["Differentiable", "Optimization", "Drone"] },
  { id: "cloth_style3d", name: "Style3D Cloth", category: "Cloth", difficulty: "Intermediate", image: "./public/examples/cloth-style3d.jpg", description: "Experiment with a garment-scale mesh and GPU-accelerated cloth dynamics.", tags: ["Garment", "Deformables", "Style3D"] },
];

const state = {
  selectedId: "basic_shapes",
  category: "All",
  query: "",
  runtime: null,
  viewerUrl: "",
  connectedRunId: null,
  bundleReady: null,
  statusTimer: null,
  logTimer: null,
  launching: false,
  frameLoaded: false,
  finishing: false,
  serviceLost: false,
  coverShownAt: 0,
  progressTimer: null,
  progress: { displayed: 0, floor: 0, ceiling: 10, phase: "starting", detail: "", elapsed: 0, hasMarkers: false },
};

// The embedded viewer connects as soon as Rerun's ports are open, which happens
// long before a heavy example (ANYmal C Walk, the cloth scenes) has finished
// downloading assets and compiling kernels. The cover stays up until the Newton
// process reports its first rendered frame so an empty viewer never shows.
const STREAM_SETTLE_MS = 600;
const MARKERLESS_FALLBACK_MS = 20000;
// Give up waiting on the viewer's own canvas eventually, so a wasm failure
// leaves the user looking at Rerun's error rather than at this cover forever.
const BUNDLE_FALLBACK_MS = 90000;

const el = {};
let toastTimer;

function selectedExample() {
  return examples.find((example) => example.id === state.selectedId) || examples[0];
}

function refreshIcons() {
  if (window.lucide) window.lucide.createIcons();
}

function visibleExamples() {
  const query = state.query.trim().toLowerCase();
  return examples.filter((example) => {
    const categoryMatch = state.category === "All" || state.category === example.category;
    const searchable = `${example.name} ${example.id} ${example.category} ${example.description} ${example.tags.join(" ")}`.toLowerCase();
    return categoryMatch && (!query || searchable.includes(query));
  });
}

function renderFilters() {
  const categories = ["All", ...new Set(examples.map((example) => example.category))];
  el.filters.innerHTML = categories.map((category) => `
    <button class="filter-button ${category === state.category ? "active" : ""}" type="button" data-category="${category}" aria-pressed="${category === state.category}">${category}</button>
  `).join("");
}

function renderExampleList() {
  const visible = visibleExamples();
  el.resultCount.textContent = `${visible.length} shown`;
  el.emptyState.hidden = visible.length > 0;
  el.exampleList.hidden = visible.length === 0;
  el.exampleList.innerHTML = visible.map((example) => `
    <button class="example-item ${example.id === state.selectedId ? "selected" : ""}" type="button" data-example-id="${example.id}" aria-pressed="${example.id === state.selectedId}">
      <span class="example-thumb"><img src="${example.image}" alt="" loading="lazy" /></span>
      <span class="example-copy"><strong>${example.name}</strong><span>${example.category} · ${example.difficulty}</span></span>
      <i data-lucide="check" aria-hidden="true"></i>
    </button>
  `).join("");
  refreshIcons();
}

function updateSelectedContent() {
  const example = selectedExample();
  el.inspectorTitle.textContent = example.name;
  el.selectedId.textContent = example.id;
  el.selectedImage.src = example.image;
  el.selectedImage.alt = `${example.name} example preview`;
  el.selectedCategory.textContent = example.category;
  el.selectedDescription.textContent = example.description;
  el.selectedTags.innerHTML = example.tags.map((tag) => `<span class="meta-tag">${tag}</span>`).join("");
  el.selectedCommand.textContent = `python -m newton.examples ${example.id} --viewer rerun --device cuda:0`;
  el.sourceLink.href = `https://github.com/newton-physics/newton/search?q=${encodeURIComponent(example.id)}&type=code`;
  updateRunButton();
}

function selectExample(id) {
  if (!examples.some((example) => example.id === id)) return;
  state.selectedId = id;
  renderExampleList();
  updateSelectedContent();
}

function buildViewerUrl(runtime) {
  // The launcher reverse-proxies the Rerun web viewer at /viewer and its gRPC
  // message proxy at the same origin, so only the launcher port needs to be
  // exposed and the viewer is always same-origin. Explicit origins remain
  // supported for advanced setups that expose the Rerun ports directly.
  const scheme = window.location.protocol === "https:" ? "https" : "http";
  const sameOrigin = `${scheme}://${window.location.host}`;
  const grpcOrigin = (runtime?.grpc_origin || sameOrigin).replace(/\/+$/, "");
  const viewerBase = (runtime?.viewer_origin || `${window.location.origin}/viewer`).replace(/\/+$/, "");
  const proxy = `rerun+${grpcOrigin}/proxy`;
  // Force a renderer: WebGPU is often unavailable in remote/proxied browser
  // tabs, and Rerun's WebGPU->WebGL fallback fails ("canvas already in use").
  // WebGL is the most compatible default; override via ?renderer= in the URL.
  const renderer = new URLSearchParams(window.location.search).get("renderer") || "webgl";
  // Presence of hide_welcome_screen (any value) suppresses Rerun's example
  // gallery that otherwise flashes before the live stream attaches.
  return `${viewerBase}/?url=${encodeURIComponent(proxy)}&renderer=${encodeURIComponent(renderer)}&hide_welcome_screen`;
}

function updateRunButton() {
  const current = state.runtime?.example === state.selectedId && state.runtime?.viewer_ready;
  el.runButton.querySelector("span").textContent = current ? "Restart this example" : "Run this example";
  el.runButton.disabled = state.launching;
}

function showLoading(title, copy) {
  el.loadingTitle.textContent = title;
  el.loadingCopy.textContent = copy;
  el.loadingProgress.classList.remove("failed");
  if (el.loadingCover.hidden) {
    el.loadingCover.hidden = false;
    state.progress.displayed = 0;
  }
  state.coverShownAt = Date.now();
  startProgressTicker();
}

function hideLoading() {
  state.progress.displayed = 100;
  renderProgress();
  window.clearInterval(state.progressTimer);
  state.progressTimer = null;
  el.loadingCover.hidden = true;
}

function startProgressTicker() {
  if (state.progressTimer) return;
  let ticks = 0;
  state.progressTimer = window.setInterval(() => {
    const { ceiling, displayed } = state.progress;
    // No component of the stack reports a completion ratio, so within a phase
    // the bar eases toward that phase's ceiling and never past it.
    if (displayed < ceiling) state.progress.displayed = displayed + (ceiling - displayed) * 0.04;
    state.progress.elapsed += 0.14;
    state.bundleReady = viewerBundleReady();
    renderProgress();
    if (++ticks % 4 === 0) fetchStatus().catch(() => {});
  }, 140);
}

function renderProgress() {
  const value = Math.max(0, Math.min(100, Math.round(state.progress.displayed)));
  el.progressFill.style.width = `${value}%`;
  el.progressTrack.setAttribute("aria-valuenow", String(value));
  el.progressPercent.textContent = `${value}%`;
  // Newton streaming frames is not the end of the wait: the browser still has to
  // fetch and start the viewer's wasm bundle, so report that phase honestly.
  const waitingOnBundle = state.progress.phase === "streaming" && state.bundleReady === false;
  el.progressPhase.textContent = waitingOnBundle
    ? "Loading the Rerun viewer bundle in your browser."
    : state.progress.detail;
  el.progressElapsed.textContent = `${Math.round(state.progress.elapsed)}s`;
}

function viewerBundleReady() {
  // The iframe's load event fires for Rerun's shell page, well before its wasm
  // viewer has been fetched and started. The viewer is same-origin through the
  // /viewer proxy, so its own canvas is the only honest readiness signal.
  try {
    const canvas = el.rerunFrame.contentDocument?.getElementById("the_canvas_id");
    if (!canvas) return null;
    return canvas.classList.contains("visible");
  } catch {
    return null;
  }
}

function applyProgress(runtime) {
  const incoming = runtime.progress;
  if (!incoming) return;
  if (state.serviceLost && runtime.running) {
    state.serviceLost = false;
    const example = examples.find((item) => item.id === runtime.example);
    el.loadingTitle.textContent = `Starting ${example?.name || runtime.example}`;
    el.loadingCopy.textContent = "Reconnected to the launcher. Attaching the embedded Rerun viewer…";
  }
  const failed = incoming.phase === "failed" || incoming.phase === "offline";
  if (!failed && !state.launching && !el.loadingCover.hidden) {
    // A run started elsewhere (another tab, the API, a container restart) still
    // owns this cover, so name the scene the runtime is actually bringing up.
    const active = examples.find((item) => item.id === runtime.example);
    if (active) el.loadingTitle.textContent = `Starting ${active.name}`;
  }
  state.progress.floor = incoming.percent;
  state.progress.ceiling = failed ? state.progress.displayed : incoming.ceiling;
  state.progress.phase = incoming.phase;
  state.progress.detail = incoming.detail;
  state.progress.elapsed = incoming.elapsed;
  state.progress.hasMarkers = incoming.has_markers;
  // Progress is monotonic per launch: a phase floor can only pull the bar up.
  if (incoming.percent > state.progress.displayed) state.progress.displayed = incoming.percent;
  el.loadingProgress.classList.toggle("failed", failed);
  renderProgress();
  maybeFinishLoading(runtime);
}

function maybeFinishLoading(runtime) {
  if (el.loadingCover.hidden || state.finishing || !state.frameLoaded || !runtime.viewer_ready) return;
  const streaming = runtime.progress?.phase === "streaming";
  const markerless =
    !runtime.progress?.has_markers && Date.now() - state.coverShownAt > MARKERLESS_FALLBACK_MS;
  if (!streaming && !markerless) return;
  const waited = Date.now() - state.coverShownAt;
  if (viewerBundleReady() === false && waited < BUNDLE_FALLBACK_MS) return;
  state.finishing = true;
  window.setTimeout(() => {
    state.finishing = false;
    hideLoading();
  }, STREAM_SETTLE_MS);
}

function connectViewer(force = false) {
  if (!state.runtime?.viewer_ready) return;
  const url = buildViewerUrl(state.runtime);
  el.openViewer.href = url;
  el.endpoint.textContent = decodeURIComponent(new URL(url).searchParams.get("url") || "");
  // Restarting the same example produces an identical URL, so the run id is what
  // distinguishes a new stream from the one the iframe is already attached to.
  if (!force && state.connectedRunId === state.runtime.run_id && state.viewerUrl === url) return;

  state.viewerUrl = url;
  state.connectedRunId = state.runtime.run_id;
  state.frameLoaded = false;
  state.bundleReady = false;
  el.rerunFrame.src = "about:blank";
  window.setTimeout(() => {
    el.rerunFrame.src = url;
  }, 120);
}

function updateRuntimeUi(runtime) {
  state.runtime = runtime;
  const active = examples.find((example) => example.id === runtime.example);

  // The header names whatever the runtime is loading, not just what it finished
  // loading, so a page opened mid-launch does not label the wrong scene.
  if (active) {
    el.viewerTitle.textContent = active.name;
    el.viewerPath.textContent = `/world/${active.id}`;
  }

  if (runtime.viewer_ready) {
    const streaming = runtime.progress?.phase === "streaming";
    el.streamLabel.textContent = streaming ? "Live" : "Loading";
    el.processStatus.innerHTML = `<span class="status-dot"></span> Running · PID ${runtime.pid}`;
    connectViewer();
  } else if (runtime.running) {
    el.streamLabel.textContent = "Connecting";
    el.processStatus.innerHTML = `<span class="status-dot"></span> Starting · PID ${runtime.pid}`;
  } else {
    el.streamLabel.textContent = "Offline";
    el.processStatus.innerHTML = `<span class="status-dot offline"></span> Offline`;
  }
  applyProgress(runtime);
  updateRunButton();
}

async function fetchStatus() {
  const response = await fetch("/api/status", { cache: "no-store" });
  if (!response.ok) throw new Error(`Runtime status failed (${response.status})`);
  const runtime = await response.json();
  updateRuntimeUi(runtime);
  return runtime;
}

async function waitForViewer(exampleId, runId, timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const runtime = await fetchStatus();
    if (runtime.error && !runtime.running) throw new Error(runtime.error);
    const isThisRun = runId === undefined || runtime.run_id === runId;
    if (isThisRun && runtime.example === exampleId && runtime.viewer_ready) return runtime;
    await new Promise((resolve) => window.setTimeout(resolve, 550));
  }
  throw new Error("Timed out waiting for the Rerun viewer");
}

async function runSelectedExample() {
  const example = selectedExample();
  state.launching = true;
  state.frameLoaded = false;
  state.progress.displayed = 0;
  state.progress.elapsed = 0;
  updateRunButton();
  showLoading(`Starting ${example.name}`, `Stopping the current scene, launching ${example.id} on CUDA, and attaching the embedded Rerun viewer…`);
  el.streamLabel.textContent = "Starting";

  try {
    const response = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ example: example.id }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to launch example");
    state.connectedRunId = null;
    await waitForViewer(example.id, payload.run_id);
    connectViewer(true);
    await refreshLog();
    showToast(`${example.name} is running in the embedded viewer`);
  } catch (error) {
    el.loadingTitle.textContent = "Launch failed";
    el.loadingCopy.textContent = error instanceof Error ? error.message : String(error);
    showToast("Newton example failed to launch");
  } finally {
    state.launching = false;
    updateRunButton();
  }
}

async function refreshLog() {
  try {
    const response = await fetch("/api/log", { cache: "no-store" });
    if (!response.ok) return;
    const payload = await response.json();
    el.runtimeLog.textContent = payload.lines?.length ? payload.lines.join("\n") : "Newton is running; no console output yet.";
    el.runtimeLog.scrollTop = el.runtimeLog.scrollHeight;
  } catch {
    el.runtimeLog.textContent = "Runtime log is unavailable.";
  }
}

function showToast(message) {
  window.clearTimeout(toastTimer);
  el.toast.textContent = message;
  el.toast.classList.add("visible");
  toastTimer = window.setTimeout(() => el.toast.classList.remove("visible"), 2400);
}

function bindEvents() {
  el.filters.addEventListener("click", (event) => {
    const button = event.target.closest("[data-category]");
    if (!button) return;
    state.category = button.dataset.category;
    renderFilters();
    renderExampleList();
  });
  el.exampleList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-example-id]");
    if (button) selectExample(button.dataset.exampleId);
  });
  el.search.addEventListener("input", (event) => {
    state.query = event.target.value;
    el.clearSearch.hidden = !state.query;
    renderExampleList();
  });
  el.clearSearch.addEventListener("click", () => {
    state.query = "";
    el.search.value = "";
    el.clearSearch.hidden = true;
    el.search.focus();
    renderExampleList();
  });
  el.resetFilters.addEventListener("click", () => {
    state.query = "";
    state.category = "All";
    el.search.value = "";
    el.clearSearch.hidden = true;
    renderFilters();
    renderExampleList();
  });
  el.runButton.addEventListener("click", runSelectedExample);
  el.refreshViewer.addEventListener("click", () => {
    if (!state.runtime?.viewer_ready) {
      showToast("The Rerun stream is not ready yet");
      return;
    }
    showLoading("Reconnecting viewer", "Attaching to the live Newton Rerun stream…");
    connectViewer(true);
  });
  el.fullscreen.addEventListener("click", async () => {
    try { await el.viewerStage.requestFullscreen(); } catch { showToast("Fullscreen is unavailable in this browser"); }
  });
  el.refreshLog.addEventListener("click", refreshLog);
  el.copyCommand.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(el.selectedCommand.textContent);
      el.copyCommand.querySelector("span").textContent = "Copied";
      showToast("Launch command copied");
      window.setTimeout(() => { el.copyCommand.querySelector("span").textContent = "Copy"; }, 1400);
    } catch {
      showToast(el.selectedCommand.textContent);
    }
  });
  el.rerunFrame.addEventListener("load", () => {
    if (el.rerunFrame.src === "about:blank") return;
    state.frameLoaded = true;
    if (state.runtime) maybeFinishLoading(state.runtime);
  });
}

function cacheElements() {
  Object.assign(el, {
    filters: document.querySelector("#filters"), exampleList: document.querySelector("#example-list"), resultCount: document.querySelector("#result-count"),
    search: document.querySelector("#example-search"), clearSearch: document.querySelector("#clear-search"), emptyState: document.querySelector("#empty-state"), resetFilters: document.querySelector("#reset-filters"),
    viewerTitle: document.querySelector("#viewer-example-title"), viewerPath: document.querySelector("#viewer-path"), streamLabel: document.querySelector("#stream-label"),
    inspectorTitle: document.querySelector("#inspector-title"), selectedId: document.querySelector("#selected-id"), selectedImage: document.querySelector("#selected-image"), selectedCategory: document.querySelector("#selected-category"), selectedDescription: document.querySelector("#selected-description"), selectedTags: document.querySelector("#selected-tags"),
    selectedCommand: document.querySelector("#selected-command"), copyCommand: document.querySelector("#copy-command"), sourceLink: document.querySelector("#source-link"), processStatus: document.querySelector("#process-status"), runButton: document.querySelector("#run-button"),
    rerunFrame: document.querySelector("#rerun-frame"), viewerStage: document.querySelector("#viewer-stage"), refreshViewer: document.querySelector("#refresh-viewer"), fullscreen: document.querySelector("#fullscreen-view"), openViewer: document.querySelector("#open-viewer"), endpoint: document.querySelector("#stream-endpoint"),
    loadingCover: document.querySelector("#loading-cover"), loadingTitle: document.querySelector("#loading-title"), loadingCopy: document.querySelector("#loading-copy"), runtimeLog: document.querySelector("#runtime-log"), refreshLog: document.querySelector("#refresh-log"), toast: document.querySelector("#toast"),
    loadingProgress: document.querySelector("#loading-progress"), progressTrack: document.querySelector("#progress-track"), progressFill: document.querySelector("#progress-fill"), progressPercent: document.querySelector("#progress-percent"), progressPhase: document.querySelector("#progress-phase"), progressElapsed: document.querySelector("#progress-elapsed"),
  });
}

async function init() {
  cacheElements();
  renderFilters();
  renderExampleList();
  updateSelectedContent();
  bindEvents();
  refreshIcons();
  state.coverShownAt = Date.now();
  startProgressTicker();
  try {
    const runtime = await fetchStatus();
    if (runtime.example && examples.some((example) => example.id === runtime.example)) {
      state.selectedId = runtime.example;
      renderExampleList();
      updateSelectedContent();
    }
    if (!runtime.viewer_ready) await waitForViewer(runtime.example || "basic_shapes");
    connectViewer(true);
    await refreshLog();
  } catch (error) {
    state.serviceLost = true;
    showLoading("Runtime service unavailable", error instanceof Error ? error.message : String(error));
    el.streamLabel.textContent = "Offline";
  } finally {
    // Polling must survive a failed first load: the launcher is often still
    // starting when the page opens, and these timers are what recover from it.
    state.statusTimer = window.setInterval(() => fetchStatus().catch(() => {}), 1500);
    state.logTimer = window.setInterval(refreshLog, 4000);
  }
}

document.addEventListener("DOMContentLoaded", init);
