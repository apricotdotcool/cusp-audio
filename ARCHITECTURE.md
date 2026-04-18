# Architecture

This document describes the module layout and runtime behavior of cusp, aimed at contributors. User-facing install and usage docs live in [README.md](README.md).

## Module map

All source lives under `src/cusp/`.

- **`cli.py`** — Click commands (`devices`, `pair`, `stream`). Parses the `-d` flag (numeric → index, literal `system` → system-audio shorthand, otherwise substring name), merges CLI args with the loaded config, and dispatches to the pipeline.
- **`config.py`** — `CuspConfig` dataclass and `load_config`. Search order for the TOML: `--config` flag, then `./cusp.toml`, then `~/.config/cusp/cusp.toml`. CLI arguments override TOML values; TOML values override dataclass defaults.
- **`audio.py`** — PCM capture. `AudioCapture` wraps a `sounddevice.InputStream` (PortAudio) for hardware inputs; `SystemAudioCapture` shells out to `parec` on Linux for system audio. Both expose the same async `read_chunks` / `paused` / `signal_stop` interface. `make_capture` picks the right one based on config.
- **`airplay.py`** — pyatv wrapper. `discover_devices` (RAOP-only scan), `pair_device` (interactive, PIN-based), `resolve_target` (scan + apply stored credentials and configured password), `connect_target` (open a pyatv connection). Credentials persist at `~/.config/cusp/credentials.json` (mode `0600`).
- **`pipeline.py`** — The orchestrator. `run_pipeline` owns the capture loop, silence-threshold gating, idle timeout, periodic target refresh, and signal handling. `StreamingSession` owns one AirPlay connection plus its WAV-framed reader. `run_with_reconnect` wraps it in an auto-reconnect loop.
- **`logging_.py`** — `setup_logging(level, file)` — thin wrapper around `logging.basicConfig`.
- **`__main__.py`** — Entry point so `python -m cusp` works alongside the `cusp` console script.

## Data flow

```
[mic / USB / system audio]
           │
           ▼
audio.make_capture → AudioCapture | SystemAudioCapture
           │  (float32 → int16 PCM chunks)
           ▼
pipeline.run_pipeline
  ├── RMS vs config.silence_threshold      (gate open/close)
  ├── airplay.resolve_target               (scan, creds, password)
  └── StreamingSession
        ├── WAV header + raw PCM → asyncio.StreamReader
        └── pyatv.stream.stream_file       (RAOP session)
           │
           ▼
    [AirPlay receiver]
```

No transcoding: cusp prepends a WAV header to the raw PCM stream and hands that to pyatv, which keeps latency down and avoids an MP3 dependency.

## Lifecycle and state machine

The pipeline has two states, toggled by input loudness:

- **Idle.** Capture is running, but no AirPlay session is open. Every chunk's mean-square is compared to `silence_threshold²`. When it exceeds the threshold, `resolve_target` is called and a `StreamingSession` is started. While idle, `refresh_target_loop` re-scans for the receiver every `target_refresh_interval` seconds so a receiver that changed IP is picked up before the next session.
- **Streaming.** PCM chunks are fed to the session's `StreamReader`, which pyatv drains. Each above-threshold chunk resets `last_activity`. After `idle_timeout` seconds with no above-threshold chunks, the session is torn down and the pipeline returns to idle.

`AudioCapture.paused()` is used as a context manager around the slow transitions (session start, session stop) so the capture queue doesn't saturate and so the consumer resumes at real time instead of playing a backlog.

## Signal handling

`run_pipeline` registers loop signal handlers for `SIGINT`, `SIGTERM`, and `SIGHUP` (where available — SIGHUP is skipped on Windows). Each sets a `stop_event`, signals the capture iterator to exit, and lets the main loop fall through to its `finally`: any live session is stopped (draining the consumer and awaiting pyatv's close tasks so the RAOP TEARDOWN is actually sent), capture is stopped, and handlers are removed. See `pipeline.run_pipeline` and `StreamingSession.stop` for the exact ordering.

## Auto-reconnect

`run_with_reconnect` is the top-level entry point from `cli.stream`. It calls `run_pipeline` in a loop: a clean signal-driven shutdown breaks out; a `ConnectionError` or other exception is logged and retried after `reconnect_delay` seconds (provided `auto_reconnect` is true — when false, the exception propagates). This handles both initial-connect failures (receiver offline at startup) and mid-stream drops.

## Extending cusp

- **New capture backend** — add a class in `audio.py` that mirrors the `AudioCapture` interface (`start`, `stop`, `read_chunks`, `paused`, `drain`, `signal_stop`) and wire it into `make_capture`. Tests for the existing backends live in `tests/test_audio.py`.
- **New output protocol** — replace or generalize `StreamingSession` in `pipeline.py`. The session needs to accept PCM chunks via `feed`, expose `failed` / `exception` so the main loop can surface mid-stream errors, and implement `stop` with a timeout-bounded teardown.
- **New config field** — add it to `CuspConfig` in `config.py`, read it from the appropriate TOML section in `load_config`, and (if user-settable from the command line) add a matching Click option in `cli.stream`. Update `cusp.toml.example` and the Configuration table in README.md.
- **New CLI command** — add a function under the `@main.command()` decorator in `cli.py`, following the `devices` / `pair` / `stream` pattern (sync Click wrapper that calls `asyncio.run` on an async implementation).
