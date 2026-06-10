from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import textwrap
import venv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _write_dependency_stubs(stub_root: Path) -> None:
    openai_types_dir = stub_root / "openai" / "types"
    openai_types_dir.mkdir(parents=True)
    (stub_root / "openai" / "__init__.py").write_text(
        textwrap.dedent(
            """
            class _ChatCompletions:
                def create(self, *args, **kwargs):
                    raise NotImplementedError


            class _ChatNamespace:
                def __init__(self):
                    self.completions = _ChatCompletions()


            class OpenAI:
                def __init__(self, *args, **kwargs):
                    self.chat = _ChatNamespace()

                def close(self):
                    return None


            class AsyncOpenAI:
                def __init__(self, *args, **kwargs):
                    self.chat = _ChatNamespace()

                async def close(self):
                    return None
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (openai_types_dir / "__init__.py").write_text("", encoding="utf-8")
    (openai_types_dir / "chat.py").write_text(
        textwrap.dedent(
            """
            class ChatCompletion:
                pass


            ChatCompletionMessageParam = dict
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    (stub_root / "httpx.py").write_text(
        textwrap.dedent(
            """
            class Response:
                status_code = 200
                text = ""
                content = b""

                def json(self):
                    return {}


            class Client:
                def __init__(self, *args, **kwargs):
                    pass

                def close(self):
                    return None


            class AsyncClient:
                def __init__(self, *args, **kwargs):
                    pass

                async def aclose(self):
                    return None


            class HTTPStatusError(Exception):
                pass
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    pil_dir = stub_root / "PIL"
    pil_dir.mkdir()
    (pil_dir / "__init__.py").write_text(
        textwrap.dedent(
            """
            class _ImageStub:
                LANCZOS = 1


            Image = _ImageStub
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.slow
def test_built_distributions_include_packaged_assets(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()

    # Build from a copy of the source tree so the test never mutates the
    # repo (a stale ./build dir would otherwise have to be deleted to keep
    # setuptools from leaking removed files into the wheel).
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    shutil.copytree(ROOT / "yutori", src_dir / "yutori", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "tests", src_dir / "tests", ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    for fname in ("pyproject.toml", "README.md", "LICENSE", "MANIFEST.in"):
        shutil.copy2(ROOT / fname, src_dir / fname)

    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist_dir)],
        cwd=src_dir,
        check=True,
    )

    # The sdist must carry a collectable test suite (MANIFEST.in grafts
    # tests/ — setuptools' default glob ships tests/test_*.py without the
    # conftest/helpers they import) and the py.typed marker.
    subprocess.run(
        [sys.executable, "-m", "build", "--sdist", "--outdir", str(dist_dir)],
        cwd=src_dir,
        check=True,
    )
    sdists = sorted(dist_dir.glob("yutori-*.tar.gz"))
    assert sdists, "expected build to produce an sdist"
    with tarfile.open(sdists[0]) as tar:
        sdist_names = tar.getnames()
    sdist_root = sdist_names[0].split("/")[0]
    for required in (
        "tests/conftest.py",
        "tests/__init__.py",
        "tests/_usage_fixtures.py",
        "yutori/py.typed",
    ):
        assert f"{sdist_root}/{required}" in sdist_names, f"sdist missing {required}"

    wheels = sorted(dist_dir.glob("yutori-*.whl"))
    assert wheels, "expected build to produce a wheel"
    wheel_path = wheels[0]

    venv_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(venv_dir)
    venv_python = venv_dir / ("Scripts" if os.name == "nt" else "bin") / "python"

    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--no-deps", str(wheel_path)],
        check=True,
    )

    stubs_dir = tmp_path / "stubs"
    _write_dependency_stubs(stubs_dir)

    verify_script = textwrap.dedent(
        """
        from importlib import resources
        from yutori.navigator._assets import load_js_asset

        # PEP 561: without this marker, type checkers ignore every inline
        # annotation in the installed package.
        assert resources.files("yutori").joinpath("py.typed").is_file()
        from yutori.navigator.tools import (
            EXECUTE_JS_SCRIPT,
            EXTRACT_ELEMENTS_SCRIPT,
            FIND_SCRIPT,
            GET_ELEMENT_BY_REF_SCRIPT,
            SET_ELEMENT_VALUE_SCRIPT,
        )
        from yutori.navigator.tools import load_tool_script

        navigator_js_dir = resources.files("yutori.navigator").joinpath("js")
        js_dir = resources.files("yutori.navigator.tools").joinpath("js")
        assert navigator_js_dir.is_dir()
        assert js_dir.is_dir()

        navigator_js_names = {
            child.name for child in navigator_js_dir.iterdir() if child.name.endswith(".js")
        }
        js_names = {child.name for child in js_dir.iterdir() if child.name.endswith(".js")}
        navigator_expected = {
            "disable_new_tabs.js",
            "disable_printing.js",
            "replace_native_select_dropdown.js",
        }
        expected = {
            "extract_elements.js",
            "find.js",
            "get_element_by_ref.js",
            "set_element_value.js",
            "execute_js.js",
        }
        assert navigator_expected.issubset(navigator_js_names)
        assert expected.issubset(js_names)

        for script_name in navigator_expected:
            assert load_js_asset(script_name).strip()

        for script in [
            EXTRACT_ELEMENTS_SCRIPT,
            FIND_SCRIPT,
            GET_ELEMENT_BY_REF_SCRIPT,
            SET_ELEMENT_VALUE_SCRIPT,
            EXECUTE_JS_SCRIPT,
        ]:
            assert script.strip()

        for script_name in expected:
            assert load_tool_script(script_name).strip()
        """
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = str(stubs_dir)
    subprocess.run([str(venv_python), "-c", verify_script], check=True, env=env, cwd=tmp_path)
