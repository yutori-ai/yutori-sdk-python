/*
 * The activity window's renderer. The Swift host calls these three functions with the
 * payloads the Python presentation controller sends: one live frame, one transcript
 * entry, and the caption shown before the first frame arrives.
 *
 * Entries are addressed by id so a shell command can be revised in place as it moves
 * from running to its exit code, instead of stacking a card per lifecycle event.
 */

const MAX_ENTRIES = 400;
const STICK_TO_BOTTOM_SLACK_PX = 40;

const transcript = document.getElementById("n2-activity-transcript");
const frame = document.getElementById("n2-activity-frame");
const frameImage = document.getElementById("n2-activity-image");
const frameCaption = document.getElementById("n2-activity-frame-caption");
const entries = new Map();

// 16x16 stroked glyphs, one per action vocabulary id the Python side sends. The
// fallback is a dot, so an action this renderer has never heard of still lines up
// with the rows around it.
const ICON_PATHS = {
  click: "M4 3l8 5-3.2 1L11 12l-1.6 1-2.3-3.1L4 12z",
  move: "M4 3l8 5-3.2 1L11 12l-1.6 1-2.3-3.1L4 12z",
  type: "M2 4.5h12v7H2z M4.6 7.2h.01 M7.2 7.2h.01 M9.8 7.2h.01 M12 7.2h.01 M5.2 9.6h5.6",
  key: "M3 4.5h10v7H3z M5.6 8h4.8",
  scroll: "M8 3v10 M4.6 9.6L8 13l3.4-3.4",
  drag: "M3.4 12.6l9-9 M12.4 8.2V3.6H7.8",
  wait: "M8 3.5a4.5 4.5 0 100 9 4.5 4.5 0 000-9z M8 5.8V8.2l1.7 1.2",
  terminal: "M3 3.5h10v9H3z M5.6 7l1.8 1.6-1.8 1.6 M9.2 10.6h2.2",
};

const SHELL_STATE_LABELS = {
  starting: "starting",
  running: "running",
  completed: "done",
  failed: "failed",
  timed_out: "timed out",
  cancelled: "cancelled",
};

// A finished command is labelled by its exit code alone; the failed and timed-out
// states are coloured amber, so the label does not need to spell out "failed".
const shellStateLabel = ({ state, exitCode }) =>
  exitCode != null && (state === "completed" || state === "failed")
    ? `exit ${exitCode}`
    : (SHELL_STATE_LABELS[state] ?? state);

const span = (className, text) => {
  const element = document.createElement("span");
  element.className = className;
  element.textContent = text;
  return element;
};

const actionIcon = (name) => {
  const path = ICON_PATHS[name];
  if (!path) return span("n2-action-icon", "•");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "n2-action-icon");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("aria-hidden", "true");
  const shape = document.createElementNS("http://www.w3.org/2000/svg", "path");
  shape.setAttribute("d", path);
  svg.appendChild(shape);
  return svg;
};

const renderShell = (element, entry) => {
  element.dataset.state = entry.state;
  const header = document.createElement("div");
  header.className = "n2-shell-header";
  header.append(
    span("n2-shell-caret", "▌"),
    span("n2-shell-label", entry.background ? "run in background" : "run command"),
    span("n2-shell-state", shellStateLabel(entry)),
  );
  const body = document.createElement("div");
  body.className = "n2-shell-body";
  const command = document.createElement("pre");
  command.className = "n2-shell-command";
  command.textContent = entry.command ?? "";
  if (entry.state === "starting" || entry.state === "running") {
    command.appendChild(span("n2-shell-cursor", ""));
  }
  body.append(span("n2-shell-prompt", "$"), command);
  element.replaceChildren(header, body);
};

const renderAction = (element, entry) => {
  const text = document.createElement("span");
  text.className = "n2-action-text";
  text.textContent = entry.text ?? "";
  element.replaceChildren(actionIcon(entry.icon), text);
};

const render = (element, entry) => {
  element.className = `n2-entry n2-entry-${entry.kind}`;
  if (entry.kind === "shell") return renderShell(element, entry);
  if (entry.kind === "action") return renderAction(element, entry);
  element.textContent = entry.text ?? "";
};

const atBottom = () =>
  transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight <= STICK_TO_BOTTOM_SLACK_PX;

/** Append an entry, or revise the one already carrying this id. */
window.__n2ActivityEntry = (entry) => {
  if (!entry || typeof entry.id !== "string" || typeof entry.kind !== "string") return { ok: false };
  // Scrolled up to read an earlier step? Then leave the viewport where the operator put it.
  const follow = atBottom();
  let element = entries.get(entry.id);
  if (element) {
    render(element, entry);
  } else {
    element = document.createElement("div");
    render(element, entry);
    entries.set(entry.id, element);
    transcript.appendChild(element);
    while (entries.size > MAX_ENTRIES) {
      const [oldest] = entries.keys();
      entries.get(oldest)?.remove();
      entries.delete(oldest);
    }
  }
  if (follow) transcript.scrollTop = transcript.scrollHeight;
  return { ok: true };
};

/** One streamed frame of the driven window, as base64 of `mediaType`. */
window.__n2ActivityFrame = ({ data, mediaType }) => {
  if (typeof data !== "string" || !data) return { ok: false };
  frameImage.src = `data:${mediaType || "image/jpeg"};base64,${data}`;
  frame.dataset.state = "live";
  return { ok: true };
};

/** What the frame area says until the first frame arrives; also what reveals it at all. */
window.__n2ActivityCaption = ({ text }) => {
  frameCaption.textContent = typeof text === "string" ? text : "";
  if (frame.dataset.state === "off") frame.dataset.state = "waiting";
  return { ok: true };
};
