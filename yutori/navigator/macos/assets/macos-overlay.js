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

window.__n2BackgroundTasks = ({ tasks, overflow }) => {
  const rail = document.getElementById("n2-background-rail");
  rail.replaceChildren();
  for (const task of tasks.slice(0, 3)) {
    const row = document.createElement("div");
    row.className = "n2-background-row";
    const title = document.createElement("div");
    title.className = "n2-background-title";
    const id = document.createElement("span");
    id.className = "n2-background-id";
    id.textContent = task.task_id;
    const state = document.createElement("span");
    state.className = "n2-background-state";
    state.textContent = task.state;
    const command = document.createElement("div");
    command.className = "n2-background-command";
    command.textContent = `$ ${task.command}`;
    title.append(id, state);
    row.append(title, command);
    rail.appendChild(row);
  }
  if (overflow > 0) {
    const more = document.createElement("div");
    more.className = "n2-background-overflow";
    more.textContent = `+${overflow} more background commands`;
    rail.appendChild(more);
  }
  return { ok: true };
};
