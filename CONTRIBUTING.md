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
2. Merge it. The "Publish yutori to PyPI" workflow runs on the version change: it runs the
   tests, builds and uploads the package, tags the merge commit `vX.Y.Z`, and creates the
   GitHub release with generated notes and the installer assets.

Do not create the tag or the release by hand. An existing `vX.Y.Z` tag tells the workflow
that version already shipped, so it skips. If a run fails after PyPI accepted the upload,
fix the cause and re-run it from the Actions tab; the upload step skips files PyPI already
has, and the tag and release steps pick up where it stopped.

## Reporting Issues

Please report issues at https://github.com/yutori-ai/yutori-sdk-python/issues
