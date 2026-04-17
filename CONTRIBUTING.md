# Contributing to cusp

Thanks for your interest in cusp! This guide should be enough to get you from a fresh clone to a green test run.

## Dev setup

cusp depends on [PortAudio](http://www.portaudio.com/) at the system level. Install it first:

```bash
# Raspberry Pi / Debian / Ubuntu
sudo apt install libportaudio2 python-dev-is-python3

# macOS
brew install portaudio
```

Then install the project and its dev dependencies with [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

`uv sync` installs the `dev` dependency group (ruff, pytest, pytest-asyncio) alongside runtime deps.

## Running tests

```bash
uv run pytest -v
```

Tests live under `tests/`. `pytest-asyncio` is configured in `asyncio_mode = "auto"`, so async tests do not need `@pytest.mark.asyncio` decorators.

## Linting and formatting

cusp uses [ruff](https://docs.astral.sh/ruff/) for both linting and formatting. CI runs the same two checks you should run locally:

```bash
uv run ruff check .
uv run ruff format --check .
```

To auto-apply formatting:

```bash
uv run ruff format .
```

Ruff configuration (target version, line length, selected lint rules) lives in `pyproject.toml` under `[tool.ruff]`.

## Pull requests

Before pushing a branch, please:

- Run `uv run ruff check .` and `uv run ruff format --check .` — CI will fail otherwise.
- Run `uv run pytest -v` and make sure everything passes.

CI runs the test suite on Python 3.10 through 3.14 and the ruff checks on 3.14. Keep changes compatible with the minimum supported version (currently 3.10, declared in `pyproject.toml`).
