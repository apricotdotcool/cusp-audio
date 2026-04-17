# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `CHANGELOG.md` and `Changelog` entry under `[project.urls]` in `pyproject.toml`.

## [0.1.1] - 2026-04-12

### Changed

- Read `__version__` from installed package metadata instead of hard-coding it
  ([APR-5](https://linear.app/apricotdotcool/issue/APR-5), [#3](https://github.com/apricotdotcool/cusp-audio/pull/3)).
- Swap `black` for `ruff` as the formatter/linter in dev dependencies; apply
  `ruff format` and `ruff check --fix` across the codebase.

### Added

- `ruff` lint job in the GitHub Actions `tests` workflow.

## [0.1.0] - 2026-04-12

### Added

- Initial release: stream audio from a microphone, USB input, or system audio
  to an AirPlay receiver, with a `cusp` CLI and optional always-on service mode.

[Unreleased]: https://github.com/apricotdotcool/cusp-audio/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/apricotdotcool/cusp-audio/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/apricotdotcool/cusp-audio/releases/tag/v0.1.0
