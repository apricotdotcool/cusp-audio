from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import numpy as np
import sounddevice as sd

if TYPE_CHECKING:
    from cusp.config import CuspConfig

logger = logging.getLogger(__name__)

SYSTEM_AUDIO = "system"


def list_input_devices() -> list[dict]:
    """Return a list of available audio input devices."""
    devices = sd.query_devices()
    results = []
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            results.append(
                {
                    "index": i,
                    "name": dev["name"],
                    "max_channels": dev["max_input_channels"],
                    "default_rate": dev["default_samplerate"],
                }
            )
    return results


def resolve_device(config: CuspConfig) -> int | str | None:
    """Resolve the configured audio device to a sounddevice device specifier.

    Returns None to use the system default, an int index, or a string name.
    Raises ValueError if a named device is not found or is ambiguous.
    """
    if config.audio_device is None:
        return None

    if isinstance(config.audio_device, int):
        return config.audio_device

    # System audio shorthand. On Linux this is handled by SystemAudioCapture
    # (via parec) and never reaches this resolver. It is unsupported on
    # other platforms — macOS users should install a virtual loopback driver
    # (Loopback or BlackHole) and select it by name from `cusp devices`.
    if config.audio_device == SYSTEM_AUDIO:
        raise ValueError(
            f"--device system is not supported on platform {sys.platform!r}"
        )

    # Substring match against input device names
    name = config.audio_device.lower()
    matches = [d for d in list_input_devices() if name in d["name"].lower()]

    if len(matches) == 0:
        raise ValueError(
            f"No audio input device matching '{config.audio_device}'. "
            "Run `cusp devices` to see available devices."
        )
    if len(matches) > 1:
        names = ", ".join(d["name"] for d in matches)
        raise ValueError(
            f"Ambiguous device '{config.audio_device}' matches: {names}. "
            "Be more specific or use the device index."
        )
    return matches[0]["index"]


class AudioCapture:
    """Captures audio from an input device and bridges to asyncio."""

    def __init__(self, config: CuspConfig, loop: asyncio.AbstractEventLoop):
        self._config = config
        self._loop = loop
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=100)
        self._stream: sd.InputStream | None = None
        self._stopped = False
        self._paused = False

    def _enqueue(self, item: bytes | None) -> None:
        """Put an item on the queue, dropping it if full.

        Must run on the event loop thread. `_callback` and `signal_stop`
        schedule this via `call_soon_threadsafe` so QueueFull is caught
        here (it cannot be caught around call_soon_threadsafe itself,
        which only schedules — it never invokes put_nowait synchronously).
        """
        if item is not None and (self._stopped or self._paused):
            return  # silently drop while shutting down or paused
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            if item is not None:
                logger.warning("Audio queue full, dropping frame")

    @contextlib.contextmanager
    def paused(self):
        """Discard incoming audio for the duration of the block, then drain.

        Use around slow operations (AirPlay session connect/disconnect) so
        the queue doesn't saturate while the consumer is blocked. The body
        may contain awaits — only the enter/exit are synchronous.
        """
        self._paused = True
        try:
            yield
        finally:
            self.drain()
            self._paused = False

    def _do_signal_stop(self) -> None:
        """Run on the loop thread: drain pending audio and post the sentinel.

        Draining first guarantees there's room for the None even if the
        queue is full, and the `_stopped` flag prevents the audio callback
        from racing more chunks in front of it.
        """
        self._stopped = True
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(None)

    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            logger.warning("Audio callback status: %s", status)
        # Copy raw float32 bytes off PortAudio's buffer (which gets reused
        # as soon as this callback returns) and defer the float→int16
        # conversion to the consumer. Keeping this thread's GIL window as
        # short as possible reduces "input overflow" status events.
        data = indata.tobytes()
        self._loop.call_soon_threadsafe(self._enqueue, data)

    async def start(self) -> None:
        device = resolve_device(self._config)

        # Infer channels and sample rate from the device when not configured.
        dev_info = sd.query_devices(device if device is not None else sd.default.device[0])
        if self._config.channels is None:
            self._config.channels = dev_info["max_input_channels"]
        if self._config.sample_rate is None:
            self._config.sample_rate = int(dev_info["default_samplerate"])

        logger.info(
            "Opening audio device: %s (rate=%d, channels=%d, blocksize=%d)",
            device if device is not None else "default",
            self._config.sample_rate,
            self._config.channels,
            self._config.blocksize,
        )
        self._stream = sd.InputStream(
            device=device,
            samplerate=self._config.sample_rate,
            channels=self._config.channels,
            blocksize=self._config.blocksize,
            dtype="float32",
            # Request the device's "high" latency preset so PortAudio
            # allocates a larger host buffer. The default ("low") leaves
            # almost no headroom, so any brief hiccup on the audio thread
            # produces an "input overflow" status and dropped frames.
            latency="high",
            callback=self._callback,
        )
        self._stream.start()
        logger.info("Audio capture started")

    async def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            logger.info("Audio capture stopped")

    async def read_chunks(self) -> AsyncIterator[bytes]:
        """Yield PCM chunks (int16) as they arrive from the audio callback.

        The audio callback enqueues raw float32 bytes; we convert to int16
        here so the per-callback work on the audio thread stays minimal.
        """
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                break
            floats = np.frombuffer(chunk, dtype=np.float32)
            pcm_int16 = (floats * 32767).clip(-32768, 32767).astype(np.int16)
            yield pcm_int16.tobytes()

    def drain(self) -> None:
        """Discard any pending chunks. Call after a slow operation (e.g.
        AirPlay session connect) to flush stale audio so the consumer
        resumes at real time instead of staying behind by the queue depth.
        """
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return

    def signal_stop(self) -> None:
        """Signal the read_chunks iterator to stop."""
        self._loop.call_soon_threadsafe(self._do_signal_stop)


class SystemAudioCapture:
    """Captures system audio on Linux via PulseAudio/PipeWire `parec`.

    Mirrors the AudioCapture interface so the pipeline can use either
    transparently. PipeWire ships a PulseAudio compatibility layer, so
    `parec` is available on essentially every modern Linux desktop.
    """

    def __init__(self, config: CuspConfig, loop: asyncio.AbstractEventLoop):
        self._config = config
        self._loop = loop
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=100)
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._paused = False

    @staticmethod
    def _default_monitor_source() -> str:
        """Return the monitor source name for the default audio sink."""
        if shutil.which("pactl") is None:
            raise RuntimeError(
                "pactl not found. Install pulseaudio-utils (or pipewire-pulse) "
                "to use --device system on Linux."
            )
        try:
            result = subprocess.run(
                ["pactl", "get-default-sink"],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"pactl get-default-sink failed: {e.stderr.strip()}"
            ) from e
        sink = result.stdout.strip()
        if not sink:
            raise RuntimeError("No default audio sink reported by pactl")
        return f"{sink}.monitor"

    async def start(self) -> None:
        if shutil.which("parec") is None:
            raise RuntimeError(
                "parec not found. Install pulseaudio-utils (or pipewire-pulse) "
                "to use --device system on Linux."
            )

        # Default to 48000/2 for system audio (no device to query).
        if self._config.sample_rate is None:
            self._config.sample_rate = 48000
        if self._config.channels is None:
            self._config.channels = 2

        source = self._default_monitor_source()
        logger.info(
            "Capturing system audio from %s (rate=%d, channels=%d)",
            source,
            self._config.sample_rate,
            self._config.channels,
        )
        self._proc = await asyncio.create_subprocess_exec(
            "parec",
            f"--device={source}",
            "--format=s16le",
            f"--rate={self._config.sample_rate}",
            f"--channels={self._config.channels}",
            "--raw",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        logger.info("System audio capture started")

    def _enqueue(self, item: bytes | None) -> None:
        """Put an item on the queue, dropping it if full."""
        if item is not None and (self._stopping or self._paused):
            return
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            if item is not None:
                logger.warning("Audio queue full, dropping frame")

    @contextlib.contextmanager
    def paused(self):
        """Discard incoming audio for the duration of the block, then drain.

        See AudioCapture.paused for the rationale.
        """
        self._paused = True
        try:
            yield
        finally:
            self.drain()
            self._paused = False

    def _do_signal_stop(self) -> None:
        """Run on the loop thread: drain pending audio and post the sentinel."""
        self._stopping = True
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(None)

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        # Match the chunk size sounddevice would produce for blocksize frames.
        block_bytes = self._config.blocksize * self._config.channels * 2  # s16le
        try:
            while True:
                chunk = await self._proc.stdout.readexactly(block_bytes)
                self._enqueue(chunk)
        except asyncio.IncompleteReadError:
            pass
        except asyncio.CancelledError:
            raise
        finally:
            if not self._stopping and self._proc is not None:
                rc = await self._proc.wait()
                stderr_bytes = b""
                if self._proc.stderr is not None:
                    with contextlib.suppress(Exception):
                        stderr_bytes = await self._proc.stderr.read()
                logger.error(
                    "parec exited unexpectedly (rc=%s): %s",
                    rc,
                    stderr_bytes.decode(errors="replace").strip(),
                )
            await self._queue.put(None)

    async def stop(self) -> None:
        self._stopping = True
        if self._proc is not None and self._proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("parec did not exit within 5s, killing")
                self._proc.kill()
                await self._proc.wait()
        if self._reader_task is not None:
            with contextlib.suppress(BaseException):
                await self._reader_task
            self._reader_task = None
        self._proc = None
        logger.info("System audio capture stopped")

    async def read_chunks(self) -> AsyncIterator[bytes]:
        """Yield PCM chunks (int16) as they arrive from parec."""
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                break
            yield chunk

    def drain(self) -> None:
        """Discard any pending chunks. See AudioCapture.drain."""
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return

    def signal_stop(self) -> None:
        """Signal the read_chunks iterator to stop."""
        self._loop.call_soon_threadsafe(self._do_signal_stop)


def make_capture(
    config: CuspConfig, loop: asyncio.AbstractEventLoop
) -> AudioCapture | SystemAudioCapture:
    """Construct an audio capture appropriate for the configured device."""
    if config.audio_device == SYSTEM_AUDIO and sys.platform.startswith("linux"):
        return SystemAudioCapture(config, loop)
    return AudioCapture(config, loop)
