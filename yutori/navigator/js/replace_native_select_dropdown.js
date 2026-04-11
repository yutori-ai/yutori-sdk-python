(() => {
  const handledSelectElementsConvergence = new WeakSet();

  const overwriteDefaultSelectConvergence = (input = null) => {
    let activeSelectElement = null;
    const rootElement = input || document.documentElement;

    function createCustomSelectElement() {
      const customSelect = document.createElement("div");
      customSelect.id = "yutori-custom-dropdown-element";
      customSelect.style.position = "absolute";
      customSelect.style.zIndex = 2147483646;
      customSelect.style.display = "none";
      document.body.appendChild(customSelect);

      const optionsList = document.createElement("div");
      optionsList.style.border = "1px solid #ccc";
      optionsList.style.backgroundColor = "#fff";
      optionsList.style.color = "black";
      customSelect.appendChild(optionsList);

      return customSelect;
    }

    function hideCustomSelect(customSelect) {
      customSelect.style.display = "none";
      activeSelectElement = null;
    }

    function showCustomSelect(select) {
      activeSelectElement = select;
      const customSelect = rootElement.querySelector("#yutori-custom-dropdown-element");
      const optionsList = customSelect.firstChild;
      optionsList.innerHTML = "";
      optionsList.style.overflowY = "auto";
      optionsList.style.maxHeight = "none";

      Array.from(select.options).forEach((option) => {
        const customOption = document.createElement("div");
        customOption.className = "custom-option";
        customOption.style.padding = "8px";
        customOption.style.cursor = "pointer";
        customOption.textContent = option.text;
        customOption.dataset.value = option.value;
        optionsList.appendChild(customOption);

        customOption.addEventListener("mouseenter", () => {
          customOption.style.backgroundColor = "#f0f0f0";
        });

        customOption.addEventListener("mouseleave", () => {
          customOption.style.backgroundColor = "";
        });

        customOption.addEventListener("mousedown", (e) => {
          e.stopPropagation();
          select.value = customOption.dataset.value;
          hideCustomSelect(customSelect);
          if (!window.location.href.includes("resy.com")) {
            select.dispatchEvent(new InputEvent("focus", { bubbles: true, cancelable: true }));
          }
          select.dispatchEvent(new InputEvent("input", { bubbles: true, cancelable: true }));
          select.dispatchEvent(new InputEvent("change", { bubbles: true, cancelable: true }));
          select.dispatchEvent(new InputEvent("blur", { bubbles: true, cancelable: true }));
        });
      });

      const selectRect = select.getBoundingClientRect();
      customSelect.style.visibility = "hidden";
      customSelect.style.display = "block";

      const margin = 8;
      const viewportWidth = window.innerWidth;
      const minWidth = Math.max(selectRect.width, 120);
      const contentWidth = optionsList.scrollWidth + 8;
      const maxWidth = Math.max(0, viewportWidth - margin * 2);
      const targetWidth = Math.min(Math.max(minWidth, contentWidth), maxWidth);

      const preferredLeft = selectRect.left + window.scrollX;
      const viewportLeft = window.scrollX + margin;
      const viewportRight = window.scrollX + viewportWidth - margin;
      const clampedLeft = Math.min(Math.max(preferredLeft, viewportLeft), viewportRight - targetWidth);

      customSelect.style.width = `${targetWidth}px`;
      customSelect.style.left = `${clampedLeft}px`;

      const optionsHeight = optionsList.scrollHeight;
      const viewportTop = window.scrollY + margin;
      const viewportBottom = window.scrollY + window.innerHeight - margin;
      const maxHeight = Math.max(0, viewportBottom - viewportTop);
      const desiredHeight = Math.min(optionsHeight, maxHeight);

      const spaceBelow = window.innerHeight - selectRect.bottom - margin;
      const spaceAbove = selectRect.top - margin;

      optionsList.style.maxHeight = `${desiredHeight}px`;

      let dropdownTop;
      if (spaceBelow >= desiredHeight) {
        dropdownTop = selectRect.bottom + window.scrollY;
      } else if (spaceAbove >= desiredHeight) {
        dropdownTop = selectRect.top + window.scrollY - desiredHeight;
      } else {
        const centeredTop = selectRect.top + window.scrollY + selectRect.height / 2 - desiredHeight / 2;
        dropdownTop = Math.min(Math.max(centeredTop, viewportTop), viewportBottom - desiredHeight);
      }

      customSelect.style.top = `${dropdownTop}px`;
      customSelect.style.visibility = "visible";
      select.focus();

      if (!optionsList.dataset.wheelHandlerAttached) {
        optionsList.addEventListener(
          "wheel",
          (event) => {
            event.preventDefault();
            optionsList.scrollTop += event.deltaY;
          },
          { passive: false },
        );
        optionsList.dataset.wheelHandlerAttached = "true";
      }

      select.addEventListener("blur", () => {
        hideCustomSelect(customSelect);
      });

      select.addEventListener("change", () => {
        hideCustomSelect(customSelect);
      });
    }

    function findSelectInShadowRoot(element) {
      return element.shadowRoot ? element.shadowRoot.querySelectorAll("select") : [];
    }

    let customSelect = rootElement.querySelector("#yutori-custom-dropdown-element");
    if (!customSelect) {
      customSelect = createCustomSelectElement();
    }

    let shadowSelects = [];
    rootElement.querySelectorAll("*").forEach((el) => {
      shadowSelects.push(...findSelectInShadowRoot(el));
    });

    const lightSelects = Array.from(rootElement.querySelectorAll("select"));
    const allSelects = [...lightSelects, ...shadowSelects];

    allSelects.forEach((select) => {
      if (select.hasAttribute("multiple")) return;
      if (!handledSelectElementsConvergence.has(select)) {
        select.addEventListener("mousedown", (e) => {
          if (!e.defaultPrevented) {
            if (customSelect.style.display === "block" && activeSelectElement === select) {
              hideCustomSelect(customSelect);
            } else {
              showCustomSelect(select);
            }
            e.preventDefault();
          }
        });
        handledSelectElementsConvergence.add(select);
      }
    });
  };

  overwriteDefaultSelectConvergence();
})();
