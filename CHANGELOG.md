# Changelog

## [Unreleased](https://github.com/apricotdotcool/cusp-audio/compare/v0.1.1...HEAD)

### Changed

- Read `__version__` from installed package metadata instead of hard-coding it
  ([#3](https://github.com/apricotdotcool/cusp-audio/pull/3)).
- Limit permissions on stored credentials file 
  ([#9](https://github.com/apricotdotcool/cusp-audio/pull/9)).
- Better documentation of systemd service in `README.md` 
  ([#10](https://github.com/apricotdotcool/cusp-audio/pull/10)).
- Swap `black` for `ruff` as the formatter/linter in dev dependencies; apply
  `ruff format` and `ruff check --fix` across the codebase.

### Added

- `ruff` lint job in the GitHub Actions `tests` workflow.
- `CHANGELOG.md` and `Changelog` entry under `[project.urls]` in `pyproject.toml` ([#8](https://github.com/apricotdotcool/cusp-audio/pull/8)).

## [0.1.1](https://github.com/apricotdotcool/cusp-audio/compare/v0.1.0...v0.1.1) - 2026-04-12

### Changed

- Fixed CLI script in `pyproject.toml`.


## [0.1.0](https://github.com/apricotdotcool/cusp-audio/releases/tag/v0.1.0) - 2026-04-12

### Added

- Initial release: stream audio from a microphone, USB input, or system audio
  to an AirPlay receiver, with a `cusp` CLI and optional always-on service mode.
