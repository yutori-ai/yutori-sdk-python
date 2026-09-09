const installN2Styles = () => {
  const source = document.getElementById("n2-host-style")?.textContent ?? "";
  for (const element of document.documentElement.querySelectorAll("*")) {
    const root = element.shadowRoot;
    if (!root || root.getElementById("n2-overlay-style")) continue;
    const style = document.createElement("style");
    style.id = "n2-overlay-style";
    style.textContent = source;
    root.appendChild(style);
  }
};

const styleSource = Array.from(document.styleSheets)
  .flatMap((sheet) => {
    try {
      return Array.from(sheet.cssRules).map((rule) => rule.cssText);
    } catch {
      return [];
    }
  })
  .join("\n");
const hostStyle = document.createElement("script");
hostStyle.id = "n2-host-style";
hostStyle.type = "text/plain";
hostStyle.textContent = styleSource;
document.head.appendChild(hostStyle);

new MutationObserver(installN2Styles).observe(document.documentElement, {
  childList: true,
  subtree: true,
});

// The shell panel hangs off the cursor capsule, so it follows the capsule for
// a while after every operation the Python side sends the bundle: moves,
// thoughts, and badges all change the capsule's frame, and the bundle animates
// those changes itself (the flip to the other side of the cursor takes several
// hundred milliseconds), so a single re-placement would land mid-flight.
// Status mode never mounts a cursor, so there the panel stays under the menu bar.
const SHELL_FOLLOW_MS = 700;
let shellFollowUntil = 0;
let shellFollowFrame = null;

const followShellRailFrame = () => {
  shellFollowFrame = null;
  positionShellRail();
  if (performance.now() < shellFollowUntil) shellFollowFrame = requestAnimationFrame(followShellRailFrame);
};

const followShellRail = () => {
  shellFollowUntil = performance.now() + SHELL_FOLLOW_MS;
  if (shellFollowFrame === null) shellFollowFrame = requestAnimationFrame(followShellRailFrame);
};

window.__n2OverlayApply = async (operation) => {
  const result = await window.__yutoriNavigatorOverlay.apply(operation);
  installN2Styles();
  positionShellRail(operation.op === "mount" || operation.immediate === true);
  followShellRail();
  return result;
};

window.__n2OverlayPulse = ({ x, y }) => {
  const pulse = document.createElement("div");
  pulse.className = "n2-overlay-click-pulse";
  pulse.style.left = `${x}px`;
  pulse.style.top = `${y}px`;
  document.getElementById("n2-overlay-effects").appendChild(pulse);
  pulse.addEventListener("animationend", () => pulse.remove(), { once: true });
  setTimeout(() => pulse.remove(), 330);
};

window.__n2EncodeObservation = async ({ data, maxLongSide, quality }) => {
  const image = await new Promise((resolve, reject) => {
    const candidate = new Image();
    candidate.onload = () => resolve(candidate);
    candidate.onerror = () => reject(new Error("PNG decode failed"));
    candidate.src = `data:image/png;base64,${data}`;
  });
  const scale = Math.min(1, maxLongSide / Math.max(image.naturalWidth, image.naturalHeight));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
  canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
  canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);
  let encoded = canvas.toDataURL("image/webp", quality);
  let format = "webp";
  if (!encoded.startsWith("data:image/webp;")) {
    encoded = canvas.toDataURL("image/jpeg", quality);
    format = "jpeg";
  }
  return { data: encoded.slice(encoded.indexOf(",") + 1), format };
};

const SHELL_STATE_LABELS = {
  starting: "starting",
  running: "running",
  completed: "done",
  failed: "failed",
  timed_out: "timed out",
  cancelled: "cancelled",
};

// A finished command is labelled by its exit code alone; the failed/timed-out
// states are coloured amber, so the label does not need to spell out "failed".
const shellStateLabel = ({ state, exit_code: exitCode }) =>
  exitCode != null && (state === "completed" || state === "failed")
    ? `exit ${exitCode}`
    : (SHELL_STATE_LABELS[state] ?? state);

const shellSpan = (className, text) => {
  const span = document.createElement("span");
  span.className = className;
  span.textContent = text;
  return span;
};

// The bundle draws the cursor and its capsule inside a shadow root.
const capsuleElements = () => {
  for (const element of document.documentElement.querySelectorAll("*")) {
    const root = element.shadowRoot;
    if (!root) continue;
    const cursor = root.querySelector(".yutori-overlay-cursor");
    const badge = root.querySelector(".yutori-overlay-badge");
    if (cursor && badge) return { cursor, badge };
  }
  return null;
};

// The capsule's frame once the cursor's move has settled: its on-screen rect,
// shifted by what the cursor's transition still has to travel (the specified
// left/top minus the animated computed values). The capsule is a child of the
// cursor, so it has the same distance left to go. `side` is which way the
// capsule extends from the badge; near the right edge the bundle flips it.
const settledCapsuleFrame = ({ cursor, badge }) => {
  const computed = getComputedStyle(cursor);
  const targetX = parseFloat(cursor.style.left) || 0;
  const targetY = parseFloat(cursor.style.top) || 0;
  const dx = targetX - (parseFloat(computed.left) || 0);
  const dy = targetY - (parseFloat(computed.top) || 0);
  const rect = badge.getBoundingClientRect();
  return {
    left: rect.left + dx,
    right: rect.right + dx,
    top: rect.top + dy,
    bottom: rect.bottom + dy,
    side: badge.dataset.side === "left" ? "left" : "right",
    aboveCursor: rect.bottom + dy <= targetY,
  };
};

const SHELL_PANEL_GAP = 8;
const SHELL_PANEL_MARGIN = 8;
// The stack is as wide as the capsule above it, so the two read as one unit;
// a capsule that has collapsed to its badge (no thought) is too narrow to
// carry a command, so the stack never goes below this.
const SHELL_PANEL_MIN_WIDTH = 280;

// Place the stack one slot further along the capsule's lane: below the capsule,
// sharing its width and its edges, on the side the capsule extends to. When the
// capsule has flipped above the cursor the stack goes above it; when the stack
// would leave the screen it takes the other side, then clamps.
const positionShellRail = (immediate = false) => {
  const elements = capsuleElements();
  if (!elements) return;
  const rail = document.getElementById("n2-shell-rail");
  rail.dataset.anchor = "cursor";
  const frame = settledCapsuleFrame(elements);
  const width = Math.max(frame.right - frame.left, SHELL_PANEL_MIN_WIDTH);
  rail.style.width = `${width}px`;
  const { height } = rail.getBoundingClientRect();
  const { innerWidth, innerHeight } = window;
  const gap = SHELL_PANEL_GAP;
  const margin = SHELL_PANEL_MARGIN;
  const fitsRight = frame.left + width <= innerWidth - margin;
  const fitsLeft = frame.right - width >= margin;
  let side = frame.side;
  if (side === "right" && !fitsRight && fitsLeft) side = "left";
  else if (side === "left" && !fitsLeft && fitsRight) side = "right";
  let left = side === "left" ? frame.right - width : frame.left;
  const fitsBelow = frame.bottom + gap + height <= innerHeight - margin;
  const fitsAbove = frame.top - gap - height >= margin;
  const above = frame.aboveCursor ? fitsAbove || !fitsBelow : !fitsBelow && fitsAbove;
  let top = above ? frame.top - gap - height : frame.bottom + gap;
  left = Math.max(margin, Math.min(left, innerWidth - margin - width));
  top = Math.max(margin, Math.min(top, innerHeight - margin - height));
  const leftStyle = `${Math.round(left * 2) / 2}px`;
  const topStyle = `${Math.round(top * 2) / 2}px`;
  if (rail.style.left === leftStyle && rail.style.top === topStyle) return;
  const transition = rail.style.transition;
  if (immediate) rail.style.transition = "none";
  rail.style.left = leftStyle;
  rail.style.top = topStyle;
  if (immediate) {
    rail.offsetHeight;
    rail.style.transition = transition;
  }
};

// One "run command" panel per shell the model is running, newest on top. A
// foreground run hangs the stack off the cursor capsule, so the operator reads
// the command where they are already looking; a background run has no cursor
// and keeps it under the menu bar's Stop item.
window.__n2ShellCommands = ({ commands, overflow }) => {
  const rail = document.getElementById("n2-shell-rail");
  rail.replaceChildren();
  for (const entry of commands) {
    const panel = document.createElement("div");
    panel.className = "n2-shell-panel";
    panel.dataset.state = entry.state;
    const header = document.createElement("div");
    header.className = "n2-shell-header";
    header.append(
      shellSpan("n2-shell-label", entry.run_in_background ? "run in background" : "run command"),
      shellSpan("n2-shell-state", shellStateLabel(entry)),
    );
    const command = document.createElement("pre");
    command.className = "n2-shell-command";
    command.textContent = entry.command;
    if (entry.state === "starting" || entry.state === "running") {
      command.appendChild(shellSpan("n2-shell-cursor", ""));
    }
    panel.append(header, command);
    rail.appendChild(panel);
  }
  if (overflow > 0) {
    const more = document.createElement("div");
    more.className = "n2-shell-overflow";
    more.textContent = `+${overflow} more command${overflow === 1 ? "" : "s"}`;
    rail.appendChild(more);
  }
  positionShellRail(true);
  return { ok: true };
};
