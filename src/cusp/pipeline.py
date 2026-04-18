from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import struct
from typing import TYPE_CHECKING

import numpy as np

from cusp.airplay import connect_target, resolve_targets
from cusp.audio import make_capture

if TYPE_CHECKING:
    from pyatv.interface import AppleTV, BaseConfig

    from cusp.config import CuspConfig

logger = logging.getLogger(__name__)


def _wav_header(sample_rate: int, channels: int, bits_per_sample: int = 16) -> bytes:
    """Build a WAV header for a continuous stream.

    Uses the maximum possible data size so pyatv/miniaudio treats this as a
    very long (but finite) WAV file and starts decoding immediately.
    """
    byte_rate = sample_rate * channels * (bits_per_sample // 8)
    block_align = channels * (bits_per_sample // 8)
    # Use 0x7FFFFFFF as data size (max signed 32-bit) to avoid EOF issues
    data_size = 0x7FFFFFFF
    file_size = 36 + data_size
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        file_size,
        b"WAVE",
        b"fmt ",
        16,  # fmt chunk size
        1,  # PCM format
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )


class StreamingSession:
    """Owns one AirPlay connection + reader + consumer task."""

    def __init__(self, atv: AppleTV, config: CuspConfig, name: str = "") -> None:
        self._atv = atv
        self._config = config
        self.name = name
        self._reader = asyncio.StreamReader(limit=2**20)  # 1MB buffer
        # Write a WAV header so pyatv can identify the audio format immediately.
        # Raw PCM data follows directly — no MP3 encode/decode round-trip needed.
        self._reader.feed_data(_wav_header(config.sample_rate, config.channels))
        self._consumer_task: asyncio.Task[None] = asyncio.create_task(self._consume())

    @classmethod
    async def start(cls, target: BaseConfig, config: CuspConfig) -> StreamingSession:
        atv = await connect_target(target)
        return cls(atv, config, name=target.name)

    async def _consume(self) -> None:
        """Stream audio data to the AirPlay receiver via pyatv."""
        await self._atv.stream.stream_file(self._reader)

    def feed(self, pcm_chunk: bytes) -> None:
        self._reader.feed_data(pcm_chunk)

    def failed(self) -> bool:
        return (
            self._consumer_task.done() and self._consumer_task.exception() is not None
        )

    def exception(self) -> BaseException | None:
        if self._consumer_task.done():
            return self._consumer_task.exception()
        return None

    async def stop(self) -> None:
        label = self.name or "AirPlay receiver"
        logger.info("Disconnecting from %s", label)
        self._reader.feed_eof()
        try:
            await asyncio.wait_for(self._consumer_task, timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("Consumer task for %s did not finish within 5s", label)
            self._consumer_task.cancel()
            with contextlib.suppress(BaseException):
                await self._consumer_task
        except Exception as e:
            logger.warning("Consumer task for %s finished with error: %s", label, e)
        finally:
            # pyatv's close() returns a set of cleanup tasks (RAOP teardown,
            # zeroconf unregister, etc). They MUST be awaited or the receiver
            # never sees the disconnect and stays "playing" until it times out.
            close_tasks = self._atv.close()
            if close_tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*close_tasks, return_exceptions=True),
                        timeout=5.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning("pyatv close for %s did not finish within 5s", label)
            logger.info("%s disconnected", label)


class GroupStreamingSession:
    """Fan out one capture stream to N AirPlay receivers.

    Owns a list of independent `StreamingSession`s (one pyatv connection +
    StreamReader + consumer task per receiver). A follower dropping mid-stream
    logs a warning but does not affect siblings; the group as a whole only
    surfaces failure once every sub-session has died, which lets the caller's
    reconnect logic run on total loss.
    """

    def __init__(self, sessions: list[StreamingSession]) -> None:
        self._sessions = sessions
        self._logged_drops: set[int] = set()

    @classmethod
    async def start(
        cls, targets: list[BaseConfig], config: CuspConfig
    ) -> GroupStreamingSession:
        """Connect all targets in parallel; keep whichever succeed."""
        results = await asyncio.gather(
            *(connect_target(t) for t in targets),
            return_exceptions=True,
        )
        sessions: list[StreamingSession] = []
        for target, result in zip(targets, results):
            if isinstance(result, BaseException):
                logger.warning(
                    "Failed to connect to AirPlay target '%s': %s",
                    target.name,
                    result,
                )
                continue
            sessions.append(StreamingSession(result, config, name=target.name))
        if not sessions:
            raise ConnectionError("Failed to connect to any configured AirPlay target")
        logger.info(
            "AirPlay group streaming to %d/%d device(s)", len(sessions), len(targets)
        )
        return cls(sessions)

    def feed(self, pcm_chunk: bytes) -> None:
        """Write `pcm_chunk` to every live sub-reader; skip any that have died."""
        for session in self._sessions:
            if session.failed():
                key = id(session)
                if key not in self._logged_drops:
                    self._logged_drops.add(key)
                    remaining = sum(1 for s in self._sessions if not s.failed())
                    logger.warning(
                        "AirPlay receiver '%s' dropped: %s; "
                        "%d device(s) still streaming",
                        session.name,
                        session.exception(),
                        remaining,
                    )
                continue
            session.feed(pcm_chunk)

    def failed(self) -> bool:
        """True only when every sub-session has failed."""
        return all(s.failed() for s in self._sessions)

    def exception(self) -> BaseException | None:
        """First sub-session exception — only returned when the whole group is dead."""
        if not self.failed():
            return None
        for s in self._sessions:
            exc = s.exception()
            if exc is not None:
                return exc
        return None

    async def stop(self) -> None:
        """Tear down every sub-session in parallel."""
        await asyncio.gather(
            *(s.stop() for s in self._sessions),
            return_exceptions=True,
        )


async def run_pipeline(config: CuspConfig) -> None:
    """Run the audio capture → AirPlay streaming pipeline.

    Capture runs continuously. AirPlay is connected lazily when input audio
    exceeds `silence_threshold`, and disconnected after `idle_timeout` seconds
    of continuous silence.
    """
    loop = asyncio.get_event_loop()

    # One-time scan at startup; refreshed periodically while idle.
    targets: list[BaseConfig] = await resolve_targets(config)
    # Guards swaps of `targets` so a concurrent reader (session start) never
    # sees a torn list during a background refresh. Group streaming in a
    # later sub-issue will rely on this.
    targets_lock = asyncio.Lock()

    capture = make_capture(config, loop)
    await capture.start()

    session: GroupStreamingSession | None = None
    last_activity: float | None = None
    threshold_sq = config.silence_threshold**2

    stop_event = asyncio.Event()

    # SIGHUP isn't defined on Windows; only register what's available.
    shutdown_signals = tuple(
        s
        for s in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", None))
        if s is not None
    )

    def _handle_signal(sig: int) -> None:
        logger.info("Received %s, shutting down", signal.Signals(sig).name)
        stop_event.set()
        capture.signal_stop()

    for sig in shutdown_signals:
        loop.add_signal_handler(sig, _handle_signal, sig)

    async def refresh_target_loop() -> None:
        """Periodically re-scan for AirPlay targets while idle."""
        nonlocal targets
        while True:
            await asyncio.sleep(config.target_refresh_interval)
            if session is not None:
                continue  # don't re-scan while a session is live
            try:
                new_targets = await resolve_targets(config)
            except ConnectionError as e:
                logger.warning("Background target refresh failed: %s", e)
                continue
            async with targets_lock:
                targets = new_targets
            logger.debug("Refreshed AirPlay targets (%d)", len(new_targets))

    refresh_task = asyncio.create_task(refresh_target_loop())

    try:
        async for pcm_chunk in capture.read_chunks():
            now = loop.time()

            # Compute mean-square on the int16 PCM, normalized to [0,1].
            samples = np.frombuffer(pcm_chunk, dtype=np.int16)
            if samples.size:
                normalized = samples.astype(np.float32) / 32768.0
                mean_sq = float(np.mean(normalized * normalized))
            else:
                mean_sq = 0.0

            if mean_sq >= threshold_sq:
                if last_activity is None:
                    logger.info("Audio detected, starting AirPlay session")
                last_activity = now

            is_active = (
                last_activity is not None
                and (now - last_activity) < config.idle_timeout
            )

            if is_active and session is None:
                # The AirPlay handshake blocks the consumer for several
                # seconds; pause the capture so the queue doesn't saturate
                # and so we resume from real time, not from a backlog.
                with capture.paused():
                    async with targets_lock:
                        current_targets = list(targets)
                    try:
                        session = await GroupStreamingSession.start(
                            current_targets, config
                        )
                    except ConnectionError:
                        # Cached targets stale — re-resolve once and retry.
                        logger.info("Cached targets stale, re-scanning")
                        new_targets = await resolve_targets(config)
                        async with targets_lock:
                            targets = new_targets
                        session = await GroupStreamingSession.start(new_targets, config)
                continue  # discard the in-hand (now stale) chunk

            if session is not None:
                # Surface any mid-stream RAOP error from the consumer task.
                if session.failed():
                    exc = session.exception()
                    await session.stop()
                    session = None
                    assert exc is not None
                    raise exc

                session.feed(pcm_chunk)

                if not is_active:
                    logger.info(
                        "Audio idle for %.0fs, closing AirPlay session",
                        config.idle_timeout,
                    )
                    # Pause the capture for the duration of the teardown
                    # (consumer drain + pyatv close tasks take seconds)
                    # so the queue doesn't saturate and warn.
                    with capture.paused():
                        await session.stop()
                    session = None
                    last_activity = None

            if stop_event.is_set():
                break
    finally:
        refresh_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await refresh_task
        if session is not None:
            with contextlib.suppress(Exception):
                await session.stop()
        await capture.stop()
        for sig in shutdown_signals:
            with contextlib.suppress(NotImplementedError, ValueError):
                loop.remove_signal_handler(sig)
        logger.info("Pipeline shut down")


async def run_with_reconnect(config: CuspConfig) -> None:
    """Run the pipeline with automatic reconnection on connection failures."""
    while True:
        try:
            await run_pipeline(config)
            break  # Clean shutdown (signal received)
        except ConnectionError as e:
            if not config.auto_reconnect:
                raise
            logger.error("Connection lost: %s", e)
            logger.info("Reconnecting in %.1fs...", config.reconnect_delay)
            await asyncio.sleep(config.reconnect_delay)
        except Exception as e:
            if not config.auto_reconnect:
                raise
            logger.error("Pipeline error: %s", e)
            logger.info("Restarting in %.1fs...", config.reconnect_delay)
            await asyncio.sleep(config.reconnect_delay)
