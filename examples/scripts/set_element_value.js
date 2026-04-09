/**
 * Sets a form input's value by element ref.
 *
 * Supports text inputs, textareas, selects, checkboxes, radios,
 * numeric/range, and date/time inputs.  Uses the native property
 * descriptor setter to bypass React/Vue interception.
 *
 * Arguments: [elementRef: string, inputValue: any]  (passed as a single array
 *   because Playwright's page.evaluate() only passes one argument)
 * Returns JSON: {success, message}
 *
 * Adapted from the n1 browser extension.
 */
(function ([elementRef, inputValue]) {
  function response(success, message) {
    return JSON.stringify({ success: success, action: "set_element_value", message: message });
  }

  function getTrackedElement(ref) {
    if (!window.__n1ElementRefs || !window.__n1ElementRefs[ref]) return null;
    var weakRef = window.__n1ElementRefs[ref];
    var element = weakRef.deref();
    if (!element || !document.contains(element)) {
      delete window.__n1ElementRefs[ref];
      return null;
    }
    return element;
  }

  function isInViewport(el) {
    var rect = el.getBoundingClientRect();
    return rect.top < window.innerHeight && rect.bottom > 0 && rect.left < window.innerWidth && rect.right > 0;
  }

  function ensureVisible(el) {
    if (!isInViewport(el)) {
      var htmlEl = document.documentElement;
      var bodyEl = document.body;
      var prevHtml = htmlEl.style.scrollBehavior;
      var prevBody = bodyEl ? bodyEl.style.scrollBehavior : "";
      try {
        htmlEl.style.scrollBehavior = "auto";
        if (bodyEl) bodyEl.style.scrollBehavior = "auto";
        el.scrollIntoView({ behavior: "instant", block: "center", inline: "center" });
        el.offsetHeight; // force layout
      } finally {
        htmlEl.style.scrollBehavior = prevHtml;
        if (bodyEl) bodyEl.style.scrollBehavior = prevBody;
      }
    }
  }

  // Use the native setter to bypass framework interception (React, Vue, etc.)
  function setNativeValue(el, value) {
    var prototype = null;
    if (el instanceof HTMLTextAreaElement) prototype = HTMLTextAreaElement.prototype;
    else if (el instanceof HTMLInputElement) prototype = HTMLInputElement.prototype;

    var descriptor = prototype ? Object.getOwnPropertyDescriptor(prototype, "value") : null;
    if (descriptor && descriptor.set) descriptor.set.call(el, value);
    else el.value = value;
  }

  function emitInputEvents(el) {
    if (document.activeElement !== el) el.focus();
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function selectOption(element, rawValue) {
    var targetValue = String(rawValue);
    for (var i = 0; i < element.options.length; i++) {
      var option = element.options[i];
      if (option.value === targetValue || option.text === targetValue) {
        element.selectedIndex = i;
        emitInputEvents(element);
        return response(true, 'Selected option "' + targetValue + '" in dropdown');
      }
    }
    var opts = [];
    for (var j = 0; j < element.options.length; j++) {
      opts.push('"' + element.options[j].text + '" (value: "' + element.options[j].value + '")');
    }
    return response(false, 'Option "' + targetValue + '" not found. Available: ' + opts.join(", "));
  }

  function updateCheckbox(element, rawValue) {
    if (typeof rawValue !== "boolean" && rawValue !== "true" && rawValue !== "false") {
      return response(false, "Checkbox requires a boolean value (true/false)");
    }
    var desiredState = rawValue === true || rawValue === "true";
    if (element.checked !== desiredState) element.click();
    if (element.checked !== desiredState) {
      return response(false, "Checkbox state did not change as requested");
    }
    return response(true, "Checkbox " + (element.checked ? "checked" : "unchecked"));
  }

  function updateRadio(element) {
    if (!element.checked) element.click();
    if (!element.checked) return response(false, "Radio button could not be selected");
    return response(true, "Radio button selected" + (element.name ? ' in group "' + element.name + '"' : ""));
  }

  function updateNumeric(element, kind, rawValue) {
    var asNumber = Number(rawValue);
    if (isNaN(asNumber) && !(kind === "number" && rawValue === "")) {
      return response(false, (kind === "range" ? "Range" : "Number") + " input requires a numeric value");
    }
    setNativeValue(element, kind === "range" ? String(asNumber) : String(rawValue));
    emitInputEvents(element);
    return response(true,
      kind === "range"
        ? "Set range to " + element.value + " (min: " + element.min + ", max: " + element.max + ")"
        : "Set number input to " + element.value
    );
  }

  function updateTextLike(element, elementType, rawValue) {
    setNativeValue(element, String(rawValue));
    if (typeof element.setSelectionRange === "function") {
      try { element.setSelectionRange(element.value.length, element.value.length); } catch (e) {}
    }
    emitInputEvents(element);
    return response(true, "Set " + elementType + ' value to "' + element.value + '"');
  }

  try {
    var element = getTrackedElement(elementRef);
    if (!element) {
      return response(false, 'No element found with reference: "' + elementRef + '". The element may have been removed from the page.');
    }

    ensureVisible(element);

    if (element instanceof HTMLSelectElement) return selectOption(element, inputValue);

    if (element instanceof HTMLInputElement) {
      var type = (element.type || "text").toLowerCase();
      if (type === "checkbox") return updateCheckbox(element, inputValue);
      if (type === "radio") return updateRadio(element);
      if (type === "date" || type === "time" || type === "datetime-local" || type === "month" || type === "week") {
        return updateTextLike(element, type, inputValue);
      }
      if (type === "range" || type === "number") return updateNumeric(element, type, inputValue);
      return updateTextLike(element, type || "text", inputValue);
    }

    if (element instanceof HTMLTextAreaElement) return updateTextLike(element, "textarea", inputValue);

    return response(false, 'Element type "' + element.tagName + '" is not a supported form input');
  } catch (error) {
    return response(false, "Error setting element value: " + (error.message || "Unknown error"));
  }
})
