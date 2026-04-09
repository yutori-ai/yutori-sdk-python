() => {
  if (!window.__n1PrintGuardInstalled) {
    window.__n1PrintGuardInstalled = true;
    try {
      Object.defineProperty(window, "print", {
        configurable: true,
        writable: true,
        value: function () {},
      });
    } catch (_) {
      try {
        window.print = function () {};
      } catch (_) {}
    }
  }

  const dropdownId = "__n1-custom-dropdown";
  const handledSelects = window.__n1HandledSelects || (window.__n1HandledSelects = new WeakSet());
  let activeSelect = window.__n1ActiveSelect || null;

  function ensureDropdownContainer() {
    const existing = document.getElementById(dropdownId);
    if (existing) {
      return existing;
    }

    const container = document.createElement("div");
    container.id = dropdownId;
    container.style.cssText = "position:absolute;z-index:2147483647;display:none;";
    document.body.appendChild(container);

    const list = document.createElement("div");
    list.style.cssText = "border:1px solid #ccc;background:#fff;color:#000;overflow-y:auto;max-height:none;";
    container.appendChild(list);

    list.addEventListener(
      "wheel",
      (event) => {
        event.preventDefault();
        list.scrollTop += event.deltaY;
      },
      { passive: false },
    );

    return container;
  }

  function hideDropdown(container) {
    container.style.display = "none";
    activeSelect = null;
    window.__n1ActiveSelect = null;
  }

  function showDropdown(select) {
    activeSelect = select;
    window.__n1ActiveSelect = select;
    const container = ensureDropdownContainer();
    const list = container.firstChild;
    list.innerHTML = "";

    for (let index = 0; index < select.options.length; index += 1) {
      const option = select.options[index];
      const item = document.createElement("div");
      item.style.cssText = "padding:8px;cursor:pointer;";
      item.textContent = option.text;
      item.dataset.value = option.value;
      list.appendChild(item);

      item.addEventListener("mouseenter", function () {
        this.style.backgroundColor = "#f0f0f0";
      });
      item.addEventListener("mouseleave", function () {
        this.style.backgroundColor = "";
      });
      item.addEventListener("mousedown", function (event) {
        event.stopPropagation();
        select.value = this.dataset.value;
        hideDropdown(container);
        select.dispatchEvent(new Event("input", { bubbles: true }));
        select.dispatchEvent(new Event("change", { bubbles: true }));
      });
    }

    const rect = select.getBoundingClientRect();
    container.style.visibility = "hidden";
    container.style.display = "block";

    const margin = 8;
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    const minWidth = Math.max(rect.width, 120);
    const contentWidth = list.scrollWidth + 8;
    const maxWidth = Math.max(0, viewportWidth - margin * 2);
    const targetWidth = Math.min(Math.max(minWidth, contentWidth), maxWidth);

    const preferredLeft = rect.left + window.scrollX;
    const viewportLeft = window.scrollX + margin;
    const viewportRight = window.scrollX + viewportWidth - margin;
    const clampedLeft = Math.min(Math.max(preferredLeft, viewportLeft), viewportRight - targetWidth);
    container.style.width = targetWidth + "px";
    container.style.left = clampedLeft + "px";

    const optionsHeight = list.scrollHeight;
    const viewportTop = window.scrollY + margin;
    const viewportBottom = window.scrollY + viewportHeight - margin;
    const maxHeight = Math.max(0, viewportBottom - viewportTop);
    const desiredHeight = Math.min(optionsHeight, maxHeight);
    list.style.maxHeight = desiredHeight + "px";

    const spaceBelow = viewportHeight - rect.bottom - margin;
    const spaceAbove = rect.top - margin;
    let dropdownTop;

    if (spaceBelow >= desiredHeight) {
      dropdownTop = rect.bottom + window.scrollY;
    } else if (spaceAbove >= desiredHeight) {
      dropdownTop = rect.top + window.scrollY - desiredHeight;
    } else {
      const centeredTop = rect.top + window.scrollY + rect.height / 2 - desiredHeight / 2;
      dropdownTop = Math.min(Math.max(centeredTop, viewportTop), viewportBottom - desiredHeight);
    }

    container.style.top = dropdownTop + "px";
    container.style.visibility = "visible";
    select.focus();

    select.addEventListener("blur", function onBlur() {
      hideDropdown(container);
      select.removeEventListener("blur", onBlur);
    });
    select.addEventListener("change", function onChange() {
      hideDropdown(container);
      select.removeEventListener("change", onChange);
    });
  }

  function patchSelects() {
    const container = ensureDropdownContainer();
    const selects = document.querySelectorAll("select:not([multiple])");
    for (const select of selects) {
      if (handledSelects.has(select)) {
        continue;
      }
      handledSelects.add(select);
      select.addEventListener("mousedown", (event) => {
        if (event.defaultPrevented) {
          return;
        }
        event.preventDefault();
        if (container.style.display === "block" && activeSelect === select) {
          hideDropdown(container);
        } else {
          showDropdown(select);
        }
      });
    }
  }

  patchSelects();

  if (document.readyState !== "complete") {
    return { ready: false };
  }

  if (window.performance && window.performance.getEntriesByType) {
    const resources = window.performance.getEntriesByType("resource");
    const pending = resources.filter((resource) => !resource.responseEnd);
    if (pending.length > 0) {
      return { ready: false };
    }
  }

  return { ready: true };
}
