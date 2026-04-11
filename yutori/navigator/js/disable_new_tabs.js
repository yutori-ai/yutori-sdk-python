(() => {
  const removeTargets = () => {
    document.querySelectorAll("[target], [formtarget]").forEach((el) => {
      const target = el.getAttribute("target") || el.getAttribute("formtarget");
      if (target && target !== "_self" && target !== "_parent" && target !== "_top") {
        el.removeAttribute("target");
        el.removeAttribute("formtarget");
      }
    });
  };
  removeTargets();

  const openDescriptor = Object.getOwnPropertyDescriptor(window, "open");
  if (!openDescriptor || openDescriptor.configurable !== false) {
    Object.defineProperty(window, "open", {
      value(url) {
        if (typeof url === "string" && url && !url.startsWith("about:")) {
          window.location.href = url;
        }
        return { closed: false, focus: () => {}, blur: () => {}, close: () => {}, postMessage: () => {} };
      },
      writable: false,
      configurable: false,
    });
  }

  if (!Element.prototype._setAttributePatched) {
    const originalSetAttribute = Element.prototype.setAttribute;
    Element.prototype.setAttribute = function (name, value) {
      if (
        (name.toLowerCase() === "target" || name.toLowerCase() === "formtarget") &&
        value &&
        value !== "_self" &&
        value !== "_parent" &&
        value !== "_top"
      ) {
        return;
      }
      return originalSetAttribute.call(this, name, value);
    };
    Element.prototype._setAttributePatched = true;
  }

  if (!HTMLFormElement.prototype._targetPatched) {
    Object.defineProperty(HTMLFormElement.prototype, "target", {
      set(val) {
        if (!val || val === "_self" || val === "_parent" || val === "_top") {
          this.setAttribute("target", val || "");
        }
      },
      get() {
        return this.getAttribute("target") || "";
      },
      configurable: true,
    });
    HTMLFormElement.prototype._targetPatched = true;
  }

  if (!HTMLAnchorElement.prototype._targetPatched) {
    Object.defineProperty(HTMLAnchorElement.prototype, "target", {
      set(val) {
        if (!val || val === "_self" || val === "_parent" || val === "_top") {
          this.setAttribute("target", val || "");
        }
      },
      get() {
        return this.getAttribute("target") || "";
      },
      configurable: true,
    });
    HTMLAnchorElement.prototype._targetPatched = true;
  }

  if (!window._submitListenerPatched) {
    document.addEventListener(
      "submit",
      (e) => {
        const target = e.target.getAttribute("target");
        if (target && target !== "_self" && target !== "_parent" && target !== "_top") {
          e.target.removeAttribute("target");
        }
      },
      true,
    );
    window._submitListenerPatched = true;
  }

  if (!window._mutationObserverPatched) {
    new MutationObserver(removeTargets).observe(document.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["target", "formtarget"],
    });
    window._mutationObserverPatched = true;
  }
})();
