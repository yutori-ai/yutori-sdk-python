# Contributing to Yutori Python SDK

Thank you for your interest in contributing to the Yutori Python SDK!

## Development Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/yutori-ai/yutori-sdk-python.git
   cd yutori-sdk-python
   ```

2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install -e ".[dev]"
   ```

3. Install pre-commit hooks (regenerates `install.sh` when its inputs change):
   ```bash
   pip install pre-commit && pre-commit install
   ```

4. Run tests:
   ```bash
   pytest
   ```

5. Run linting:
   ```bash
   ruff check .
   ruff format .
   ```

## Code Style

- We use [Ruff](https://docs.astral.sh/ruff/) for linting and formatting
- Maximum line length is 120 characters
- All code must have type annotations

## Testing

- Write tests for new functionality
- Ensure all tests pass before submitting a PR
- Tests use pytest and pytest-asyncio

## Pull Requests

1. Fork the repository
2. Create a new branch for your feature
3. Make your changes
4. Run tests and linting
5. Submit a pull request

## Releases

Before the first release, register PyPI Trusted Publishing for GitHub Actions with owner `yutori-ai`, repository
`yutori-sdk-python`, workflow `publish_to_pypi.yml`, and environment `pypi`. Then push an annotated `vX.Y.Z` tag and
run **Publish yutori to PyPI** from the GitHub Actions UI with that tag (or the tag without its `v` prefix) as its input.
The workflow builds and tests the exact tag, publishes it to PyPI, uploads every installer asset to a draft GitHub
release, and only then publishes the release. Do not publish a GitHub release manually or dispatch the workflow for an
already-published release:
`yutori.com/install.sh` follows the latest published release.

## Releasing

1. Open a PR titled `chore(release): X.Y.Z` that bumps `version` in `pyproject.toml`,
   runs `bash scripts/build_install.sh` to regenerate `install.sh`, and updates the pinned
   `yutori==` versions under `examples/navigator_n2/` (`pyproject.toml`, `README.md`,
   `Dockerfile.direct_x11`).
2. Merge it. The "Publish yutori to PyPI" workflow runs on the version change: it creates
   the annotated `vX.Y.Z` tag on the merge commit, runs the tests, builds and verifies the
   exact distributions, publishes them with PyPI Trusted Publishing, and only then creates
   the GitHub release with the checksums, provenance, and installer assets.

Do not create the tag by hand. A `vX.Y.Z` tag that points at an older commit tells the
workflow that version already shipped, so it skips. If a run fails after tagging, re-run it
from the Actions tab: the tag job reuses a tag that points at the same commit, the PyPI step
skips distributions PyPI already holds, and a published release is never overwritten.

## Reporting Issues

Please report issues at https://github.com/yutori-ai/yutori-sdk-python/issues
