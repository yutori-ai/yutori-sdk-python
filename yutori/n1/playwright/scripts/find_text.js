(rawNeedle) => {
  const SKIPPED_TAGS = { script: true, style: true, meta: true, link: true, title: true, noscript: true };
  const needle = String(rawNeedle || "").trim().toLowerCase();

  function failure(message) {
    return { success: false, message };
  }

  function ensureStore() {
    if (!window.__n1ElementRefs) window.__n1ElementRefs = {};
    if (!window.__n1ElementIds) window.__n1ElementIds = new WeakMap();
    if (!window.__n1RefCounter) window.__n1RefCounter = 0;
  }

  function compactWhitespace(value) {
    return value ? value.replace(/\\s+/g, " ").trim() : "";
  }

  function isVisible(element) {
    const style = window.getComputedStyle(element);
    return (
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      style.opacity !== "0" &&
      element.offsetWidth > 0 &&
      element.offsetHeight > 0
    );
  }

  function getOrCreateRef(element) {
    const existingRef = window.__n1ElementIds.get(element);
    if (existingRef && window.__n1ElementRefs[existingRef] && window.__n1ElementRefs[existingRef].deref() === element) {
      return existingRef;
    }
    const ref = `ref_${++window.__n1RefCounter}`;
    window.__n1ElementIds.set(element, ref);
    window.__n1ElementRefs[ref] = new WeakRef(element);
    return ref;
  }

  function getRole(element) {
    const explicitRole = element.getAttribute("role");
    if (explicitRole) return explicitRole;
    const tag = element.tagName.toLowerCase();
    const tagRoles = {
      a: "link",
      button: "button",
      input: "textbox",
      select: "combobox",
      textarea: "textbox",
      h1: "heading",
      h2: "heading",
      h3: "heading",
      h4: "heading",
      h5: "heading",
      h6: "heading",
    };
    return tagRoles[tag] || tag;
  }

  function getName(element) {
    const candidates = [
      element.getAttribute("aria-label"),
      element.getAttribute("placeholder"),
      element.getAttribute("title"),
      element.value,
      element.textContent,
    ];
    for (const candidate of candidates) {
      const normalized = compactWhitespace(candidate || "");
      if (normalized) return normalized;
    }
    return "";
  }

  function describe(element) {
    const role = getRole(element);
    const name = getName(element);
    const ref = getOrCreateRef(element);
    return `- ${role}${name ? ` "${name.slice(0, 120)}"` : ""} [ref=${ref}]`;
  }

  if (!needle) {
    return failure("find requires non-empty text");
  }

  ensureStore();
  const matches = [];
  for (const element of document.querySelectorAll("*")) {
    const tag = element.tagName.toLowerCase();
    if (SKIPPED_TAGS[tag] || !isVisible(element)) continue;
    const haystack = compactWhitespace(
      [
        element.textContent || "",
        element.getAttribute("aria-label") || "",
        element.getAttribute("placeholder") || "",
        element.getAttribute("title") || "",
        element.value || "",
      ].join(" "),
    ).toLowerCase();
    if (!haystack.includes(needle)) continue;
    matches.push({ element, line: describe(element) });
    if (matches.length >= 20) break;
  }

  if (matches.length === 0) {
    return {
      success: true,
      totalMatches: 0,
      matches: [],
      message: `No visible matches found for "${rawNeedle}".`,
    };
  }

  const first = matches[0].element;
  const htmlElement = document.documentElement;
  const bodyElement = document.body;
  const previousHtmlScrollBehavior = htmlElement.style.scrollBehavior;
  const previousBodyScrollBehavior = bodyElement ? bodyElement.style.scrollBehavior : "";
  try {
    htmlElement.style.scrollBehavior = "auto";
    if (bodyElement) bodyElement.style.scrollBehavior = "auto";
    first.scrollIntoView({ behavior: "instant", block: "center", inline: "center" });
    first.offsetHeight;
  } finally {
    htmlElement.style.scrollBehavior = previousHtmlScrollBehavior;
    if (bodyElement) bodyElement.style.scrollBehavior = previousBodyScrollBehavior;
  }

  return {
    success: true,
    totalMatches: matches.length,
    matches: matches.map((match) => match.line),
    message: `Found ${matches.length} visible match${matches.length === 1 ? "" : "es"} for "${rawNeedle}".`,
  };
}
