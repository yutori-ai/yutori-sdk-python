from __future__ import annotations

import importlib
import sys
import warnings

import pytest

COMPAT_IMPORTS = [
    ("yutori.n1", "denormalize_coordinates"),
    ("yutori.n1.keys", "map_key_to_playwright"),
    ("yutori.n1.payload", "trimmed_messages_to_fit"),
    ("yutori.n1.loop", "update_trimmed_history"),
    ("yutori.n1.page_ready", "PageReadyChecker"),
    ("yutori.n1.replay", "TrajectoryRecorder"),
    ("yutori.n1.hooks", "RunHooksBase"),
    ("yutori.n1.images", "screenshot_to_data_url"),
    ("yutori.n1.content", "extract_text_content"),
    ("yutori.n1.context", "format_task_with_context"),
    ("yutori.n1.coordinates", "normalize_coordinates"),
    ("yutori.n1.models", "N1_MODEL"),
    ("yutori.n1.stop", "format_stop_and_summarize"),
    ("yutori.n1._assets", "load_js_asset"),
]


def _fresh_import(module_name: str):
    for name in list(sys.modules):
        if name == "yutori.n1" or name.startswith("yutori.n1."):
            sys.modules.pop(name, None)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        module = importlib.import_module(module_name)

    assert any(issubclass(item.category, DeprecationWarning) for item in caught)
    return module


@pytest.mark.parametrize("module_name, attribute_name", COMPAT_IMPORTS)
def test_compat_imports_forward_and_warn(module_name: str, attribute_name: str) -> None:
    module = _fresh_import(module_name)
    assert hasattr(module, attribute_name)


def test_package_exposes_submodules_as_attributes() -> None:
    # The pre-rename package imported submodules eagerly, so attribute
    # access worked right after a plain `import yutori.n1`.
    module = _fresh_import("yutori.n1")
    for submodule in ("payload", "images", "loop", "replay", "page_ready", "models"):
        assert getattr(module, submodule).__name__ == f"yutori.n1.{submodule}"
    with pytest.raises(AttributeError):
        module.does_not_exist


def test_shim_modules_expose_names_outside_dunder_all() -> None:
    # `__all__` only affects star-imports; direct imports of unexported
    # names worked from the pre-rename modules and must keep working.
    module = _fresh_import("yutori.n1.page_ready")
    assert hasattr(module, "SupportsAsyncPageReady")
    assert hasattr(module, "logger")


def test_shim_modules_preserve_dunder_all() -> None:
    import yutori.navigator.page_ready as target

    module = _fresh_import("yutori.n1.page_ready")
    assert module.__all__ == target.__all__

