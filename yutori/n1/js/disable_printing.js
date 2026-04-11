(() => {
  "use strict";
  if (window.__printGuardInstalled__) return;
  window.__printGuardInstalled__ = true;

  const log = (...args) => {
    try {
      console.debug("[print-guard]", ...args);
    } catch {}
  };
  const noop = () => log("window.print() intercepted");

  try {
    Object.defineProperty(window, "print", { configurable: true, writable: true, value: noop });
  } catch {
    try {
      window.print = noop;
    } catch {}
  }

  log("print-guard installed");
})();
