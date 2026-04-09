/**
 * Resolves an element ref string to viewport pixel coordinates.
 *
 * If the element's center is outside the viewport, it is scrolled into view
 * first. Returns JSON: {success, coordinates?[x,y], message?}.
 *
 * Adapted from the n1 browser extension.
 */
(function (elementRef) {
  function failure(message) {
    return JSON.stringify({ success: false, action: "get_element_by_ref", message: message });
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

  function isViewportVisible(rect) {
    var vw = window.innerWidth || document.documentElement.clientWidth;
    var vh = window.innerHeight || document.documentElement.clientHeight;
    return rect.top < vh && rect.bottom > 0 && rect.left < vw && rect.right > 0 && rect.width > 0 && rect.height > 0;
  }

  function isPointInViewport(x, y) {
    var vw = window.innerWidth || document.documentElement.clientWidth;
    var vh = window.innerHeight || document.documentElement.clientHeight;
    return x >= 0 && x <= vw && y >= 0 && y <= vh;
  }

  try {
    var element = getTrackedElement(elementRef);
    if (!element) {
      return failure('No element found with reference: "' + elementRef + '". The element may have been removed from the page.');
    }

    var beforeScrollRect = element.getBoundingClientRect();
    var wasVisibleBeforeScroll = isViewportVisible(beforeScrollRect);
    var centerX = beforeScrollRect.left + beforeScrollRect.width / 2;
    var centerY = beforeScrollRect.top + beforeScrollRect.height / 2;
    var centerInViewport = isPointInViewport(centerX, centerY);

    if (!wasVisibleBeforeScroll || !centerInViewport) {
      var htmlEl = document.documentElement;
      var bodyEl = document.body;
      var prevHtml = htmlEl.style.scrollBehavior;
      var prevBody = bodyEl ? bodyEl.style.scrollBehavior : "";
      try {
        htmlEl.style.scrollBehavior = "auto";
        if (bodyEl) bodyEl.style.scrollBehavior = "auto";
        element.scrollIntoView({ behavior: "instant", block: "center", inline: "center" });
        element.offsetHeight; // force layout
      } finally {
        htmlEl.style.scrollBehavior = prevHtml;
        if (bodyEl) bodyEl.style.scrollBehavior = prevBody;
      }
    }

    var rect = element.getBoundingClientRect();
    return JSON.stringify({
      success: true,
      coordinates: [rect.left + rect.width / 2, rect.top + rect.height / 2],
    });
  } catch (error) {
    return failure("Error finding element by reference: " + (error.message || "Unknown error"));
  }
})
