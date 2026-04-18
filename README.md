# cusp

[![CI](https://github.com/apricotdotcool/cusp-audio/actions/workflows/tests.yml/badge.svg)](https://github.com/apricotdotcool/cusp-audio/actions/workflows/tests.yml)
[![PyPI version](https://img.shields.io/pypi/v/cusp-audio.svg)](https://pypi.org/project/cusp-audio/)
[![Python versions](https://img.shields.io/pypi/pyversions/cusp-audio.svg)](https://pypi.org/project/cusp-audio/)
[![License](https://img.shields.io/pypi/l/cusp-audio.svg)](https://pypi.org/project/cusp-audio/)

Stream audio from a microphone, USB input, or system audio to an AirPlay receiver. Designed to run headlessly on a Raspberry Pi, but should work on any Linux or macOS computer.

Can be run as an always-on service which will connect to an AirPlay receiver when audio starts and disconnect when it stops. Hook up a turntable and use a HomePod as its speaker.

Named in honor of the great band [Cusp](https://cusptunes.bandcamp.com).

## Install

### System dependencies

```bash
# Raspberry Pi / Debian / Ubuntu
sudo apt install libportaudio2 python-dev-is-python3

# macOS
brew install portaudio
```

To capture **system audio** with `-d system` on **Linux**, you need PulseAudio or PipeWire with `parec` available. On Debian/Ubuntu: `sudo apt install pulseaudio-utils` (or `pipewire-pulse` on PipeWire systems — usually already present).

`-d system` is not supported on macOS. To capture system audio there, install a virtual loopback driver such as [Loopback by Rogue Amoeba](https://rogueamoeba.com/loopback/) or [BlackHole](https://existential.audio/blackhole/), then select the driver by name from `cusp devices` like any other input.

### Install cusp

cusp is published on PyPI as `cusp-audio`.

```bash
# pip
pip install cusp-audio

# uv
uv tool install cusp-audio
```

## Usage

### List available devices

```bash
cusp devices
```

Shows audio input devices and AirPlay receivers on the network.

### Stream audio

```bash
# From a named input device
cusp stream -d "USB Audio" -t "Living Room"

# From the system audio output on Linux (see "System audio" below)
cusp stream -d system -t "Living Room"

# Or use a config file
cusp stream --config cusp.toml
```

The `-d` flag accepts:
- a device name (substring match) or index number from `cusp devices`
- the literal `system` to capture system audio output (Linux only)

The `-t` flag accepts an AirPlay receiver name.

### Pair with a device

Some AirPlay receivers require pairing before streaming:

```bash
cusp pair "Living Room"
```

Credentials are stored in `~/.config/cusp/credentials.json`.

### All stream options

```
cusp stream [OPTIONS]

  -d, --device TEXT       Audio input device name, index, or "system" (Linux)
  -t, --target TEXT       AirPlay receiver name
  -c, --config PATH       Config file path
      --sample-rate INT   Sample rate in Hz (default: from device)
      --channels INT      Number of channels (default: from device)
      --log-level TEXT    DEBUG, INFO, WARNING, or ERROR
      --log-file TEXT     Log to file instead of stderr
```

## System audio

### Linux

`cusp stream -d system` captures whatever your machine is currently playing and sends it to the AirPlay target. cusp shells out to `parec` and captures from the default audio sink's monitor source. This works on essentially every modern Linux desktop — PipeWire ships a PulseAudio compatibility layer, so `parec` is available there too. No configuration needed.

### macOS

`-d system` is not supported on macOS — passing it will exit with an error. macOS does not expose system audio as a capturable input by default, so you need to install a virtual loopback driver and then select it by name from `cusp devices` like any other input.

**With Loopback (recommended)**: install [Loopback by Rogue Amoeba](https://rogueamoeba.com/loopback/), open it, click *New Virtual Device*, and configure the source application or process.

**With BlackHole**: install [BlackHole](https://existential.audio/blackhole/), open *Audio MIDI Setup*, create a *Multi-Output Device* containing both your speakers and BlackHole, and set that Multi-Output Device as the system output.

## Configuration

Copy the example config and edit it:

```bash
cp cusp.toml.example cusp.toml
```

```toml
[audio]
device = "USB Audio"   # or "system", or an index number
# sample_rate and channels are inferred from the selected device.
# Uncomment to override:
# sample_rate = 48000
# channels = 2
blocksize = 1024

[airplay]
target = "Living Room"
# password = "secret"

[behavior]
auto_reconnect = true
reconnect_delay = 5.0
# How loud the input has to be (0.0–1.0 RMS) before we open the AirPlay
# session. Lower = more sensitive.
silence_threshold = 0.01
# Seconds of continuous silence before we tear down the AirPlay session.
idle_timeout = 30.0
# How often (seconds) to re-scan for the AirPlay target while idle, so a
# receiver that changed IP is picked up before the next session.
target_refresh_interval = 300.0
log_level = "INFO"
# log_file = "/var/log/cusp.log"
```

Config file search order: `--config` flag, then `./cusp.toml`, then `~/.config/cusp/cusp.toml`. Command line arguments override config file values.

## Running on a Raspberry Pi

### Systemd service

First, confirm where `cusp` is installed — the unit's `ExecStart` must point at the actual binary path:

```bash
which cusp
```

Installs via `uv tool install` or `pip install --user` land in `~/.local/bin/cusp`, not `/usr/local/bin/cusp`. Use whatever path `which` reports in the `ExecStart=` lines below.

#### User service (recommended)

Running cusp as a `--user` service avoids root, matches how `uv tool install` / `pip install --user` place the binary, and is the simplest setup on a Raspberry Pi.

```bash
uv tool install cusp-audio
```

Create `~/.config/systemd/user/cusp.service`:

```ini
[Unit]
Description=Cusp AirPlay Audio Streamer
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=%h/.local/bin/cusp stream --config %h/.config/cusp/cusp.toml
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

Then enable and start it:

```bash
systemctl --user enable --now cusp
loginctl enable-linger $USER   # keep the service running after logout
```

#### System service

If you'd rather run cusp as a dedicated service account, create `/etc/systemd/system/cusp.service`:

```ini
[Unit]
Description=Cusp AirPlay Audio Streamer
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=cusp
ExecStart=/usr/local/bin/cusp stream --config /etc/cusp/cusp.toml
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Point `ExecStart` at wherever `cusp` is actually installed for the `cusp` user — if you installed cusp with `pip install --user` or `uv tool install` as the `cusp` user, that path will be under its home directory, not `/usr/local/bin`. Installing system-wide (e.g. with `sudo pip install cusp-audio`) is the simplest way to get `/usr/local/bin/cusp`.

Then enable and start it:

```bash
sudo systemctl enable cusp
sudo systemctl start cusp
```

systemd sends `SIGTERM` on `systemctl stop`, which cusp handles gracefully — the AirPlay session is torn down cleanly so the receiver returns to idle immediately instead of waiting for its own session timeout.

### Audio permissions

The user running cusp must be in the `audio` group — whether that's your login user (for a `--user` service) or the dedicated `cusp` service account:

```bash
sudo usermod -aG audio cusp
```

## How it works

cusp captures PCM audio via PortAudio/sounddevice for hardware inputs or `parec` for system audio on Linux, and streams it to an AirPlay receiver over RAOP using pyatv. The audio is sent as raw PCM with a WAV header — no MP3 encode/decode round-trip — which keeps latency down and avoids a transcoding dependency.

**Connect on demand.** Capture runs continuously, but the AirPlay session is only opened once incoming audio exceeds `silence_threshold`. The default threshold should ignore normal input line noise, but is sensitive enough to pick up the scratches before music starts when playing vinyl. After `idle_timeout` seconds of continuous silence, the session is torn down and the receiver is released. The next burst of audio reconnects automatically. This means you can leave cusp running 24/7 without monopolizing the AirPlay target.

**Clean shutdown.** `SIGINT` (Ctrl-C), `SIGTERM` (`kill`), and `SIGHUP` (terminal close) all trigger a graceful shutdown that flushes the audio buffer, sends the RAOP TEARDOWN, and waits for pyatv's pending close tasks to complete before exiting. The receiver sees the disconnect immediately rather than waiting for its session timeout.

**Auto-reconnect.** If the AirPlay connection drops mid-stream, cusp logs the error, waits `reconnect_delay` seconds, and tries again. The target is also re-scanned periodically while idle so a receiver that changed IP is picked up before the next session.

Expected latency is 2–3 seconds due to RAOP protocol buffering.

See [ARCHITECTURE.md](ARCHITECTURE.md) for module-level details and how to extend cusp.

## Troubleshooting

### Choppy or skipped audio

If playback sounds choppy or drops samples, try increasing `blocksize` in `cusp.toml` (e.g. from 1024 to 2048 or 4096). Larger blocks give the capture and network paths more headroom to absorb jitter. This adds latency, but only on the order of milliseconds — negligible next to the 2–3 seconds of RAOP buffering already in the pipeline.

### Mono input devices

By default cusp infers the channel count from the selected device, so mono devices work automatically. If you've explicitly set `channels` in `cusp.toml` or via `--channels` to a value the device doesn't support, capture will fail to open. Remove the override or set it to match the device. Run `cusp devices` to see what each device reports.

### Tailscale and `--accept-routes`

If you run Tailscale on your devices as I do, `--accept-routes` can break AirPlay discovery and streaming. When enabled, Tailscale installs routes that cause traffic to the AirPlay receiver to be sent back out over the Tailscale interface toward the receiver's Tailscale IP instead of reaching it directly on the LAN. The receiver ends up unreachable, discovery is flaky, and streams fail to start. If you hit this, either disable `--accept-routes` on the machine running cusp, or exclude your LAN subnet from the accepted routes.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, test, and lint instructions.
