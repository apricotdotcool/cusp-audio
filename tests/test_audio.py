import asyncio
import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

from cusp.audio import (
    AudioCapture,
    SystemAudioCapture,
    list_input_devices,
    make_capture,
    resolve_device,
)
from cusp.config import CuspConfig


FAKE_DEVICES = [
    {"name": "Built-in Microphone", "max_input_channels": 2, "max_output_channels": 0, "default_samplerate": 44100.0},
    {"name": "USB Audio Device", "max_input_channels": 2, "max_output_channels": 0, "default_samplerate": 48000.0},
    {"name": "HDMI Output", "max_input_channels": 0, "max_output_channels": 8, "default_samplerate": 48000.0},
    {"name": "USB Audio Pro", "max_input_channels": 4, "max_output_channels": 2, "default_samplerate": 96000.0},
]


@pytest.fixture
def mock_devices(monkeypatch):
    monkeypatch.setattr("cusp.audio.sd.query_devices", lambda: FAKE_DEVICES)


class TestResolveDevice:
    def test_none_returns_none(self, mock_devices):
        assert resolve_device(CuspConfig(audio_device=None)) is None

    def test_int_returns_int(self, mock_devices):
        assert resolve_device(CuspConfig(audio_device=3)) == 3

    def test_system_raises_on_non_linux(self, mock_devices, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        with pytest.raises(ValueError, match="not supported on platform"):
            resolve_device(CuspConfig(audio_device="system"))

    def test_name_match(self, mock_devices):
        result = resolve_device(CuspConfig(audio_device="Built-in"))
        assert result == 0

    def test_name_no_match(self, mock_devices):
        with pytest.raises(ValueError, match="No audio input device"):
            resolve_device(CuspConfig(audio_device="Nonexistent"))

    def test_name_ambiguous(self, mock_devices):
        with pytest.raises(ValueError, match="Ambiguous"):
            resolve_device(CuspConfig(audio_device="USB Audio"))

    def test_name_case_insensitive(self, mock_devices):
        result = resolve_device(CuspConfig(audio_device="built-in"))
        assert result == 0


class TestListInputDevices:
    def test_filters_to_input_only(self, mock_devices):
        devices = list_input_devices()
        assert len(devices) == 3
        names = [d["name"] for d in devices]
        assert "HDMI Output" not in names
        assert "Built-in Microphone" in names

    def test_device_fields(self, mock_devices):
        devices = list_input_devices()
        d = devices[0]
        assert "index" in d
        assert "name" in d
        assert "max_channels" in d
        assert "default_rate" in d


class TestAudioCaptureQueue:
    @pytest.fixture
    def capture(self):
        loop = asyncio.new_event_loop()
        cap = AudioCapture(CuspConfig(), loop)
        yield cap
        loop.close()

    def test_enqueue_drops_when_stopped(self, capture):
        capture._stopped = True
        capture._enqueue(b"data")
        assert capture._queue.empty()

    def test_enqueue_drops_when_paused(self, capture):
        capture._paused = True
        capture._enqueue(b"data")
        assert capture._queue.empty()

    def test_enqueue_passes_sentinel_when_stopped(self, capture):
        capture._stopped = True
        capture._enqueue(None)
        assert capture._queue.get_nowait() is None

    def test_enqueue_drops_when_full(self, capture):
        for i in range(100):
            capture._enqueue(b"x")
        capture._enqueue(b"overflow")
        assert capture._queue.qsize() == 100

    def test_drain_empties_queue(self, capture):
        for _ in range(5):
            capture._enqueue(b"data")
        assert not capture._queue.empty()
        capture.drain()
        assert capture._queue.empty()

    def test_paused_context_manager(self, capture):
        capture._enqueue(b"pre")
        with capture.paused():
            assert capture._paused is True
            capture._enqueue(b"during")
        assert capture._paused is False
        assert capture._queue.empty()

    def test_do_signal_stop(self, capture):
        capture._enqueue(b"a")
        capture._enqueue(b"b")
        capture._do_signal_stop()
        assert capture._stopped is True
        assert capture._queue.qsize() == 1
        assert capture._queue.get_nowait() is None


class TestAudioCaptureAsync:
    async def test_read_chunks_yields_and_stops(self):
        loop = asyncio.get_event_loop()
        capture = AudioCapture(CuspConfig(), loop)
        a = np.array([[0.0, 0.0]], dtype=np.float32).tobytes()
        b = np.array([[0.5, -0.5]], dtype=np.float32).tobytes()
        capture._enqueue(a)
        capture._enqueue(b)
        capture._enqueue(None)

        chunks = []
        async for chunk in capture.read_chunks():
            chunks.append(chunk)
        assert len(chunks) == 2
        assert np.frombuffer(chunks[0], dtype=np.int16).tolist() == [0, 0]
        assert np.frombuffer(chunks[1], dtype=np.int16).tolist() == [16383, -16383]

    async def test_start_opens_stream(self, monkeypatch):
        loop = asyncio.get_event_loop()
        capture = AudioCapture(CuspConfig(), loop)
        mock_stream = MagicMock()
        monkeypatch.setattr("cusp.audio.resolve_device", lambda cfg: None)
        monkeypatch.setattr(
            "cusp.audio.sd.InputStream",
            lambda **kwargs: mock_stream,
        )
        await capture.start()
        mock_stream.start.assert_called_once()

    async def test_stop_closes_stream(self, monkeypatch):
        loop = asyncio.get_event_loop()
        capture = AudioCapture(CuspConfig(), loop)
        mock_stream = MagicMock()
        monkeypatch.setattr("cusp.audio.resolve_device", lambda cfg: None)
        monkeypatch.setattr(
            "cusp.audio.sd.InputStream",
            lambda **kwargs: mock_stream,
        )
        await capture.start()
        await capture.stop()
        mock_stream.stop.assert_called_once()
        mock_stream.close.assert_called_once()


class TestFloat32ToInt16Conversion:
    """The float32 → int16 conversion happens in read_chunks (not the audio
    callback) so the audio thread stays as quick as possible. These tests
    exercise that conversion through the queue path."""

    async def _convert(self, indata: np.ndarray) -> np.ndarray:
        loop = asyncio.get_event_loop()
        capture = AudioCapture(CuspConfig(), loop)
        capture._enqueue(indata.tobytes())
        capture._enqueue(None)
        chunks = [c async for c in capture.read_chunks()]
        assert len(chunks) == 1
        return np.frombuffer(chunks[0], dtype=np.int16)

    async def test_float32_to_int16(self):
        indata = np.array([[0.0], [0.5], [-0.5], [1.0], [-1.0]], dtype=np.float32)
        result = await self._convert(indata)
        assert result.tolist() == [0, 16383, -16383, 32767, -32767]

    async def test_clipping(self):
        indata = np.array([[1.5], [-1.5]], dtype=np.float32)
        result = await self._convert(indata)
        assert result.tolist() == [32767, -32768]


class TestMakeCapture:
    def test_default_returns_audio_capture(self):
        loop = asyncio.new_event_loop()
        cap = make_capture(CuspConfig(), loop)
        assert isinstance(cap, AudioCapture)
        loop.close()

    def test_system_on_linux(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        loop = asyncio.new_event_loop()
        cap = make_capture(CuspConfig(audio_device="system"), loop)
        assert isinstance(cap, SystemAudioCapture)
        loop.close()

    def test_system_on_nonlinux(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        loop = asyncio.new_event_loop()
        cap = make_capture(CuspConfig(audio_device="system"), loop)
        assert isinstance(cap, AudioCapture)
        loop.close()
