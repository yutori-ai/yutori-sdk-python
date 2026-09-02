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

window.__n2OverlayApply = async (operation) => {
  const result = await window.__yutoriNavigatorOverlay.apply(operation);
  installN2Styles();
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

// One phosphor "run command" panel per shell the model is running, newest on
// top, stacked under the menu bar so the operator can read what is being
// sent to this Mac without following the cursor capsule.
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
      shellSpan("n2-shell-caret", "▌"),
      shellSpan("n2-shell-label", entry.run_in_background ? "run in background" : "run command"),
      shellSpan("n2-shell-state", shellStateLabel(entry)),
    );
    const body = document.createElement("div");
    body.className = "n2-shell-body";
    const command = document.createElement("pre");
    command.className = "n2-shell-command";
    command.textContent = entry.command;
    if (entry.state === "starting" || entry.state === "running") {
      command.appendChild(shellSpan("n2-shell-cursor", ""));
    }
    body.append(shellSpan("n2-shell-prompt", "$"), command);
    panel.append(header, body);
    rail.appendChild(panel);
  }
  if (overflow > 0) {
    const more = document.createElement("div");
    more.className = "n2-shell-overflow";
    more.textContent = `+${overflow} more command${overflow === 1 ? "" : "s"}`;
    rail.appendChild(more);
  }
  return { ok: true };
};
