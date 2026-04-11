# Plan: Rename n1 → navigator and add importable n1.5 tool scripts

## Background

The SDK's `yutori/n1/` module bundles utilities for building browser agents with
the n1 and n1.5 APIs: screenshot preparation, coordinate conversion, key mapping,
payload management, lifecycle hooks, and more.

Two problems:

1. **Naming**: The package path `yutori.n1` and the extras name `yutori[n1]` are
   awkward now that n1.5 exists (and future model versions will follow). The name
   should reflect the capability ("navigator") not the model version.

2. **Tool scripts**: n1.5's expanded tool set adds four DOM-interacting tools
   (`extract_elements`, `find`, `set_element_value`, `execute_js`) that need
   client-side JavaScript executed in the browser via Playwright's
   `page.evaluate()`. Reference JS implementations currently live in
   `examples/tools/` — they cannot be imported by downstream projects. As a
   result, `frontend-visualqa` has copy-pasted and diverged its own copies
   (~900 lines in `page_scripts.py`).

## What n1.5 introduces

The expanded tool set (`browser_tools_expanded-20260403`) adds 4 tools on top of
the 18 core browser tools. The core tools are coordinate-based visual actions
(click, scroll, type, etc.) that the API injects server-side. The expanded tools
interact with the DOM and require client-side JavaScript:

| Tool | Purpose | Client-side JS |
|------|---------|---------------|
| `extract_elements` | DOM tree walker → ref-annotated accessibility tree | Yes — recursive walk, WeakRef tracking, role/name extraction |
| `find` | Text search across visible DOM elements | Yes — querySelectorAll with visibility checks, ref creation |
| `set_element_value` | Set form inputs by element ref | Yes — native setter + event dispatch, handles all input types |
| `execute_js` | Run arbitrary JavaScript | Thin wrapper — AsyncFunction constructor + error handling |

Supporting script (not a model-callable tool):
- `get_element_by_ref` — resolves `ref=ref_N` tokens to pixel coordinates with
  scroll-into-view. Used by all coordinate actions when the model passes a `ref`
  parameter alongside or instead of coordinates.

---

## Changes

### 1. Package rename: `yutori/n1/` → `yutori/navigator/`

Rename the directory. All existing modules move as-is:

```
yutori/navigator/
├── __init__.py        ← updated docstring
├── _assets.py         ← updated files() package reference
├── content.py
├── context.py
├── coordinates.py
├── hooks.py
├── images.py          ← Pillow guard removed (see §2)
├── keys.py
├── loop.py
├── models.py
├── page_ready.py
├── payload.py
├── replay.py
├── stop.py
└── js/                ← existing page-ready scripts, unchanged
    ├── disable_new_tabs.js
    ├── disable_printing.js
    └── replace_native_select_dropdown.js
```

Internal string references to update:
- `_assets.py`: `files("yutori.n1")` → `files("yutori.navigator")`
- `__init__.py`: module docstring — "n1 and n1.5 APIs" → "navigator APIs"
- `loop.py`: module docstring references "n1 agent loops"
- `replay.py`: module docstring references "n1/n1.5 browser loops"
- `page_ready.py`: module docstring references "n1 agent loops"

**README.md** and **api.md**: replace all `from yutori.n1 import` with
`from yutori.navigator import`, and remove `pip install "yutori[n1]"` lines
(Pillow is now a base dependency; see §2).

### 2. Dependency changes

**Pillow becomes a required dependency.** Currently it's gated behind the
`n1` optional extra (`pip install "yutori[n1]"`). Since screenshot helpers are
core to every navigator agent loop, the extra adds friction for no real benefit.

Changes to `pyproject.toml`:
- Add `"pillow>=10.0.0"` to the main `dependencies` list.
- Delete the `n1 = ["pillow>=10.0.0"]` optional-dependencies group entirely.
- Remove `"pillow>=10.0.0"` from the `examples` extra (now redundant since it's
  a base dependency).
- Add `"build>=1.2.0"` to the `dev` optional-dependencies group so
  `python -m build --wheel` is available for the packaging smoke test in §7.

Changes to `yutori/navigator/images.py`:
- Remove the `try/except ImportError` lazy-import guard around `from PIL import Image`.
- Import `PIL` unconditionally at the top of the file.
- Delete the `ImportError` message that referenced `pip install 'yutori[n1]'`.

### 3. Backwards compatibility shims

Create `yutori/n1/` as a compat package that re-exports from `yutori.navigator`.
This lets existing `from yutori.n1 import ...` and `from yutori.n1.keys import ...`
continue to work while emitting `DeprecationWarning`.

**Package root shim** — `yutori/n1/__init__.py`:
```python
import warnings
warnings.warn(
    "yutori.n1 has been renamed to yutori.navigator. "
    "Update your imports to 'from yutori.navigator import ...'",
    DeprecationWarning,
    stacklevel=2,
)
from yutori.navigator import *  # noqa: F401,F403
from yutori.navigator import __all__  # noqa: F401
```

**Submodule shims** — one file per public submodule. Every submodule that
downstream code imports directly gets a shim:

| Shim file | Forwards to |
|-----------|-------------|
| `yutori/n1/keys.py` | `yutori.navigator.keys` |
| `yutori/n1/payload.py` | `yutori.navigator.payload` |
| `yutori/n1/loop.py` | `yutori.navigator.loop` |
| `yutori/n1/page_ready.py` | `yutori.navigator.page_ready` |
| `yutori/n1/replay.py` | `yutori.navigator.replay` |
| `yutori/n1/hooks.py` | `yutori.navigator.hooks` |
| `yutori/n1/images.py` | `yutori.navigator.images` |
| `yutori/n1/content.py` | `yutori.navigator.content` |
| `yutori/n1/context.py` | `yutori.navigator.context` |
| `yutori/n1/coordinates.py` | `yutori.navigator.coordinates` |
| `yutori/n1/models.py` | `yutori.navigator.models` |
| `yutori/n1/stop.py` | `yutori.navigator.stop` |
| `yutori/n1/_assets.py` | `yutori.navigator._assets` |

Each shim follows the same pattern:
```python
import warnings as _w
_w.warn("yutori.n1.keys → yutori.navigator.keys", DeprecationWarning, stacklevel=2)
from yutori.navigator.keys import *  # noqa: F401,F403
```

### 4. Tool scripts subpackage

Create `yutori/navigator/tools/` to hold the n1.5 expanded tool JS and Python
helpers:

```
yutori/navigator/tools/
├── __init__.py
├── _loader.py
└── js/
    ├── extract_elements.js
    ├── find.js
    ├── get_element_by_ref.js
    ├── set_element_value.js
    └── execute_js.js
```

**`_loader.py`** — uses the same `importlib.resources` pattern as the existing
`yutori/navigator/_assets.py`:

```python
import functools
from importlib.resources import files

@functools.lru_cache(maxsize=None)
def load_tool_script(name: str) -> str:
    """Load a JS tool script by filename from the js/ directory."""
    return files(__package__).joinpath("js", name).read_text(encoding="utf-8")
```

**`__init__.py`** — public API:

Script constants (loaded on first access via the loader):
- `EXTRACT_ELEMENTS_SCRIPT` — DOM tree walker
- `FIND_SCRIPT` — text search across visible elements
- `GET_ELEMENT_BY_REF_SCRIPT` — ref → pixel coordinate resolver
- `SET_ELEMENT_VALUE_SCRIPT` — universal form input handler
- `EXECUTE_JS_SCRIPT` — async JS execution wrapper

Helper functions:
- `evaluate_tool_script(page, script, *args) -> dict` — async. Builds an IIFE
  call expression with JSON-serialized args, calls `page.evaluate()`, normalizes
  the result via `coerce_result()`.
- `coerce_result(raw) -> dict` — normalize `page.evaluate()` output (str, dict,
  None) into a consistent dict. Handles JSON-string returns from the IIFE scripts
  and direct-dict returns from modern Playwright.

**No re-export from `yutori/navigator/__init__.py`.** Tool scripts are a distinct
concern from core navigator utilities. Downstream code imports them explicitly:
```python
from yutori.navigator.tools import EXTRACT_ELEMENTS_SCRIPT, evaluate_tool_script
```

**Package data** — add to `pyproject.toml`:
```toml
[tool.setuptools.package-data]
"yutori.navigator.tools" = ["js/*.js"]
"yutori.navigator" = ["js/*.js"]
```

The second line covers the existing page-ready JS files in `yutori/navigator/js/`.

### 5. JS scripts: move, write, and rename globals

**Move 3 existing scripts** from `examples/tools/` into `yutori/navigator/tools/js/`:
- `examples/tools/extract_dom_elements.js` → `extract_elements.js` (renamed to
  match the n1.5 tool name)
- `examples/tools/get_element_by_ref.js` → `get_element_by_ref.js`
- `examples/tools/set_element_value.js` → `set_element_value.js`

**Write 2 new scripts:**
- `find.js` — text search across visible DOM elements. Assigns refs to matches,
  scrolls first match into view, returns `{success, totalMatches, matches, message}`.
  Reference: frontend-visualqa's `FIND_TEXT_SCRIPT` (page_scripts.py:574-711).
- `execute_js.js` — async JS execution wrapper. Constructs an AsyncFunction,
  awaits it, serializes the return value. Returns `{success, hasResult, result}`
  or `{success: false, message}`. Reference: frontend-visualqa's
  `EXECUTE_JS_SCRIPT` (page_scripts.py:876-900).

**Rename browser-side globals** in all 5 tool scripts:
- `window.__n1ElementRefs` → `window.__yutoriElementRefs`
- `window.__n1ElementIds` → `window.__yutoriElementIds`
- `window.__n1RefCounter` → `window.__yutoriRefCounter`

Why `__yutori` (not `__navigator`): these globals are a Yutori platform convention
shared across models and tools. `__yutori` is stable regardless of future model
naming. The existing page-ready scripts in `yutori/navigator/js/` already use
`yutori-custom-dropdown-element` as their DOM ID prefix.

Why rename now: these globals must be consistent across all scripts that read/write
them. No consumer has shipped with the `__n1` versions — the SDK's `examples/tools/`
scripts are new and unimported, and frontend-visualqa's n1.5 transition was stashed.

**Delete `examples/tools/`** after the move.

### 6. Update examples

**All examples** — update imports from `yutori.n1` to `yutori.navigator`:
- `examples/n1.py`: `from yutori.n1 import ...` → `from yutori.navigator import ...`,
  `from yutori.n1.loop import ...` → `from yutori.navigator.loop import ...`, etc.
- `examples/n1_5.py`: same import updates, plus replace local JS loading.
- `examples/n1_custom_tools.py`: same import updates.
- `examples/n1_memo.py`: same import updates.

**`examples/n1_5.py` specifically** — replace the local file-loading pattern:
```python
# Remove:
_TOOLS_DIR = Path(__file__).parent / "tools"
@functools.lru_cache(maxsize=None)
def _load_js(name: str) -> str: ...
async def _evaluate_js(page, name: str, *args) -> any: ...

# Replace with:
from yutori.navigator.tools import (
    EXTRACT_ELEMENTS_SCRIPT,
    FIND_SCRIPT,
    GET_ELEMENT_BY_REF_SCRIPT,
    SET_ELEMENT_VALUE_SCRIPT,
    EXECUTE_JS_SCRIPT,
    evaluate_tool_script,
)
```

Update call sites:
- `_evaluate_js(page, "extract_dom_elements.js", filter_type)` →
  `evaluate_tool_script(page, EXTRACT_ELEMENTS_SCRIPT, filter_type)`
- `_evaluate_js(page, "get_element_by_ref.js", ref)` →
  `evaluate_tool_script(page, GET_ELEMENT_BY_REF_SCRIPT, ref)`
- `_evaluate_js(page, "set_element_value.js", ref, value)` →
  `evaluate_tool_script(page, SET_ELEMENT_VALUE_SCRIPT, ref, value)`
- Replace the custom `find` branch to call
  `evaluate_tool_script(page, FIND_SCRIPT, text)` and preserve the existing
  response formatting around match counts / match lines.
- Replace the direct `page.evaluate(js_code)` branch for `execute_js` with
  `evaluate_tool_script(page, EXECUTE_JS_SCRIPT, js_code)` and preserve the
  existing output shaping for `undefined`, structured JSON values, and strings.

### 7. Tests

**Rename existing test files:**
- `test_n1_coordinates.py` → `test_navigator_coordinates.py`
- `test_n1_images.py` → `test_navigator_images.py`
- `test_n1_keys.py` → `test_navigator_keys.py`
- `test_n1_models.py` → `test_navigator_models.py`
- `test_n1_payload.py` → `test_navigator_payload.py`
- `test_n1_content.py` → `test_navigator_content.py`
- `test_n1_hooks.py` → `test_navigator_hooks.py`
- `test_n1_page_ready.py` → `test_navigator_page_ready.py`
- `test_n1_replay.py` → `test_navigator_replay.py`

Update all imports inside them (`from yutori.n1` → `from yutori.navigator`).
Also update `test_client.py` and `test_async_client.py` which import from
`yutori.n1`.

**New: `test_navigator_tools.py`** — tool scripts subpackage:
- Each `*_SCRIPT` constant loads as a non-empty string.
- Expected markers are present (e.g., `__yutoriElementRefs` in
  `EXTRACT_ELEMENTS_SCRIPT`, `AsyncFunction` in `EXECUTE_JS_SCRIPT`).
- `coerce_result()` handles: dict passthrough, JSON string → dict,
  plain string → `{"value": ...}`, None → `{}`.

**New: `test_n1_compat.py`** — backwards compatibility:
- `from yutori.n1 import denormalize_coordinates` works and emits
  `DeprecationWarning`.
- Every submodule shim works: `from yutori.n1.keys import map_key_to_playwright`,
  `from yutori.n1.loop import update_trimmed_history`,
  `from yutori.n1.page_ready import PageReadyChecker`, etc.
- Each emits `DeprecationWarning`.

**New: `test_packaging.py`** — built-artifact smoke test:
- Build the wheel (`python -m build --wheel`).
- Install into a temporary venv.
- Verify `from yutori.navigator.tools import EXTRACT_ELEMENTS_SCRIPT` succeeds.
- Verify the JS files are physically present using
  `importlib.resources.files("yutori.navigator.tools").joinpath("js")`.
- This catches the failure mode where `pyproject.toml` package-data is wrong and
  `.js` files are missing from the built wheel.

### 8. Downstream migration: frontend-visualqa (separate PR)

The SDK ships first with compat shims so frontend-visualqa's `main` branch
continues to work. Then a separate PR against frontend-visualqa migrates it.

**Current state** (on frontend-visualqa `main`):
- Imports `yutori.n1` in 4 files: `actions.py`, `navigator_client.py`,
  `hook_adapter.py`, `n1_client.py`
- Maintains legacy `n1_client.py` / `N1Client` alongside `navigator_client.py` /
  `NavigatorClient`
- Duplicates ~600 lines of SDK-owned JS in `page_scripts.py`:
  `GET_ELEMENT_BY_REF_SCRIPT`, `EXTRACT_ELEMENTS_SCRIPT`,
  `SET_ELEMENT_VALUE_SCRIPT`, `FIND_TEXT_SCRIPT`, `EXECUTE_JS_SCRIPT`

**Migration steps:**

1. **Bump yutori SDK dependency** in `pyproject.toml` to the version containing
   the rename.

2. **Update all SDK imports** — `from yutori.n1 import ...` →
   `from yutori.navigator import ...` across all files.

3. **Remove `n1_client.py`** — `N1Client` is superseded by `NavigatorClient`.
   Delete the file. Update `__init__.py` exports, `runner.py` lazy-load, and
   `claim_verifier.py` type references to use only `NavigatorClient`.

4. **Replace duplicated tool scripts in `page_scripts.py`** — delete the 5
   SDK-owned script constants. Import them from `yutori.navigator.tools`.
   Keep `PREPARE_PAGE_SCRIPT` — that's frontend-visualqa-specific.

5. **Update `actions.py`** — replace inline `page.evaluate(SCRIPT, args)` calls
   for SDK-owned scripts with `evaluate_tool_script()`.

6. **Rename `__n1*` → `__yutori*` in app-owned JS** — `PREPARE_PAGE_SCRIPT`
   references `__n1PrintGuardInstalled` and other `__n1*` names. Rename those
   to `__yutori*` for consistency with the SDK scripts.

7. **Update tests** — fix imports, verify `NavigatorClient` is the only client
   path, remove `N1Client` test coverage.

---

## File inventory

### New files

| File | Purpose |
|------|---------|
| `yutori/navigator/*` | Renamed module (all files moved from `yutori/n1/`) |
| `yutori/navigator/tools/__init__.py` | Public API for tool scripts |
| `yutori/navigator/tools/_loader.py` | `importlib.resources` script loader |
| `yutori/navigator/tools/js/extract_elements.js` | DOM tree walker (moved + renamed globals) |
| `yutori/navigator/tools/js/find.js` | Text search (new) |
| `yutori/navigator/tools/js/get_element_by_ref.js` | Ref → coordinates (moved + renamed globals) |
| `yutori/navigator/tools/js/set_element_value.js` | Form input handler (moved + renamed globals) |
| `yutori/navigator/tools/js/execute_js.js` | Async JS wrapper (new) |
| `yutori/n1/__init__.py` | Backwards-compat shim (package root) |
| `yutori/n1/keys.py` | Compat shim |
| `yutori/n1/payload.py` | Compat shim |
| `yutori/n1/loop.py` | Compat shim |
| `yutori/n1/page_ready.py` | Compat shim |
| `yutori/n1/replay.py` | Compat shim |
| `yutori/n1/hooks.py` | Compat shim |
| `yutori/n1/images.py` | Compat shim |
| `yutori/n1/content.py` | Compat shim |
| `yutori/n1/context.py` | Compat shim |
| `yutori/n1/coordinates.py` | Compat shim |
| `yutori/n1/models.py` | Compat shim |
| `yutori/n1/stop.py` | Compat shim |
| `yutori/n1/_assets.py` | Compat shim |
| `tests/test_navigator_tools.py` | Tool script loading tests |
| `tests/test_n1_compat.py` | Backwards-compat shim tests |
| `tests/test_packaging.py` | Wheel artifact smoke test |

### Modified files

| File | Change |
|------|--------|
| `pyproject.toml` | Add Pillow to base deps, remove `n1` extra, drop Pillow from `examples` extra, add `build` to `dev` extra, add package-data for JS |
| `README.md` | Update import paths, remove `yutori[n1]` install instructions |
| `api.md` | Update import paths, remove `yutori[n1]` install instructions |
| `yutori/navigator/__init__.py` | Update docstring |
| `yutori/navigator/_assets.py` | `files("yutori.n1")` → `files("yutori.navigator")` |
| `yutori/navigator/images.py` | Remove Pillow `ImportError` guard, import unconditionally |
| `examples/n1.py` | `yutori.n1` → `yutori.navigator` imports |
| `examples/n1_5.py` | `yutori.n1` → `yutori.navigator` imports, replace local JS loading with SDK tool imports |
| `examples/n1_custom_tools.py` | `yutori.n1` → `yutori.navigator` imports |
| `examples/n1_memo.py` | `yutori.n1` → `yutori.navigator` imports |
| `tests/test_client.py` | `yutori.n1` → `yutori.navigator` imports |
| `tests/test_async_client.py` | `yutori.n1` → `yutori.navigator` imports |
| `tests/test_n1_*.py` → `tests/test_navigator_*.py` | Rename files, update imports |

### Deleted files

| File | Reason |
|------|--------|
| `examples/tools/extract_dom_elements.js` | Moved to `yutori/navigator/tools/js/` |
| `examples/tools/get_element_by_ref.js` | Moved to `yutori/navigator/tools/js/` |
| `examples/tools/set_element_value.js` | Moved to `yutori/navigator/tools/js/` |
| `examples/tools/` | Empty after moves |

## Not in scope

- **`PREPARE_PAGE_SCRIPT`**: frontend-visualqa-specific. The SDK has its own
  page-ready architecture (`PageReadyChecker` + scripts in `yutori/navigator/js/`).
- **Higher-level action executors** (e.g., `async def click_element(page, ref)`):
  downstream projects own their action execution logic. The SDK provides building
  blocks (scripts + evaluate helper), not a full executor.

## Execution order

1. Rename `yutori/n1/` directory → `yutori/navigator/`. Update internal string
   references (`_assets.py`, `__init__.py` docstring, `loop.py` docstrings).
2. Make Pillow a required dependency: update `pyproject.toml` (add to base deps,
   remove `n1` extra, drop from `examples` extra, add `build` to the `dev`
   extra for the packaging smoke test). Remove the `ImportError` guard in
   `images.py` — import Pillow unconditionally at the top of the file.
3. Create compat shims: `yutori/n1/__init__.py` (package root) + one shim per
   public submodule (keys, payload, loop, page_ready, replay, hooks, images,
   content, context, coordinates, models, stop, _assets).
4. Rename test files `test_n1_*.py` → `test_navigator_*.py`. Update all imports
   in test files (including `test_client.py` and `test_async_client.py`). Run the
   test suite to verify the rename + shims work.
5. Create `yutori/navigator/tools/` subpackage: `_loader.py` and `__init__.py`
   with the script constants and helper functions. Create the empty `js/`
   directory. Add package-data declarations to `pyproject.toml`.
6. Move the 3 existing JS files from `examples/tools/` into
   `yutori/navigator/tools/js/`, renaming `extract_dom_elements.js` →
   `extract_elements.js`. Write the 2 new scripts (`find.js`, `execute_js.js`).
   In all 5 scripts, rename `__n1*` globals → `__yutori*`. Delete
   `examples/tools/`.
7. Update all example files: `yutori.n1` → `yutori.navigator` imports. In
   `examples/n1_5.py`, replace the local `_load_js`/`_evaluate_js` pattern with
   SDK tool imports.
8. Update `README.md` and `api.md`: replace all `yutori.n1` references and
   `yutori[n1]` install instructions.
9. Write new tests: `test_navigator_tools.py` (script loading, `coerce_result`),
   `test_n1_compat.py` (every shim emits `DeprecationWarning`),
   `test_packaging.py` (build wheel, install, verify JS files present).
10. Run full test suite. Build wheel. Verify `pip install` and JS asset presence.
11. (Separate PR, after SDK release) Migrate frontend-visualqa per §8.
