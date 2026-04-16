import asyncio
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cusp.config import CuspConfig
from cusp.pipeline import StreamingSession, _wav_header


class TestWavHeader:
    def test_length(self):
        header = _wav_header(48000, 2)
        assert len(header) == 44

    def test_structure(self):
        header = _wav_header(48000, 2, 16)
        (
            riff,
            file_size,
            wave,
            fmt,
            fmt_size,
            audio_fmt,
            channels,
            sample_rate,
            byte_rate,
            block_align,
            bits_per_sample,
            data,
            data_size,
        ) = struct.unpack("<4sI4s4sIHHIIHH4sI", header)

        assert riff == b"RIFF"
        assert wave == b"WAVE"
        assert fmt == b"fmt "
        assert data == b"data"
        assert fmt_size == 16
        assert audio_fmt == 1  # PCM
        assert channels == 2
        assert sample_rate == 48000
        assert bits_per_sample == 16
        assert byte_rate == 48000 * 2 * 2
        assert block_align == 2 * 2

    def test_data_size(self):
        header = _wav_header(48000, 2)
        data_size = struct.unpack_from("<I", header, 40)[0]
        assert data_size == 0x7FFFFFFF

    def test_file_size(self):
        header = _wav_header(48000, 2)
        file_size = struct.unpack_from("<I", header, 4)[0]
        assert file_size == 36 + 0x7FFFFFFF

    def test_different_params(self):
        header = _wav_header(44100, 1, 16)
        parsed = struct.unpack("<4sI4s4sIHHIIHH4sI", header)
        # indices: 0=RIFF 1=filesize 2=WAVE 3=fmt 4=fmtsize
        #   5=audiofmt 6=channels 7=samplerate 8=byterate
        #   9=blockalign 10=bitspersample 11=data 12=datasize
        assert parsed[6] == 1  # channels
        assert parsed[7] == 44100  # sample_rate
        assert parsed[8] == 44100 * 1 * 2  # byte_rate
        assert parsed[9] == 1 * 2  # block_align


class TestStreamingSession:
    @pytest.fixture
    def mock_atv(self):
        atv = MagicMock()
        atv.stream.stream_file = AsyncMock()
        atv.close.return_value = set()
        return atv

    async def test_init_feeds_wav_header(self, mock_atv):
        config = CuspConfig(sample_rate=48000, channels=2)
        session = StreamingSession(mock_atv, config)
        # Let the consumer task get scheduled.
        await asyncio.sleep(0)
        mock_atv.stream.stream_file.assert_called_once()
        reader = mock_atv.stream.stream_file.call_args[0][0]
        assert isinstance(reader, asyncio.StreamReader)
        await session.stop()

    async def test_feed(self, mock_atv):
        config = CuspConfig(sample_rate=48000, channels=2)
        session = StreamingSession(mock_atv, config)
        session.feed(b"\x00" * 100)
        # No error means data was accepted by the StreamReader.
        await session.stop()

    async def test_failed_false_initially(self, mock_atv):
        config = CuspConfig(sample_rate=48000, channels=2)
        # Make stream_file block forever so the task stays running.
        never_done = asyncio.get_event_loop().create_future()
        mock_atv.stream.stream_file = AsyncMock(side_effect=lambda _: never_done)
        session = StreamingSession(mock_atv, config)
        await asyncio.sleep(0)
        assert not session.failed()
        never_done.cancel()
        await session.stop()

    async def test_failed_true_on_exception(self, mock_atv):
        error = ConnectionError("lost connection")
        mock_atv.stream.stream_file = AsyncMock(side_effect=error)
        config = CuspConfig(sample_rate=48000, channels=2)
        session = StreamingSession(mock_atv, config)
        # Let the consumer task finish.
        await asyncio.sleep(0.01)
        assert session.failed()
        assert session.exception() is error
        await session.stop()

    async def test_stop_calls_close(self, mock_atv):
        config = CuspConfig(sample_rate=48000, channels=2)
        session = StreamingSession(mock_atv, config)
        await session.stop()
        mock_atv.close.assert_called_once()


class TestRunWithReconnect:
    async def test_clean_exit(self):
        config = CuspConfig()
        with patch("cusp.pipeline.run_pipeline", new_callable=AsyncMock) as mock_run:
            from cusp.pipeline import run_with_reconnect

            await run_with_reconnect(config)
            mock_run.assert_awaited_once_with(config)

    async def test_retries_on_connection_error(self):
        config = CuspConfig(auto_reconnect=True, reconnect_delay=0.0)
        call_count = 0

        async def fake_pipeline(cfg):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("lost")

        with (
            patch("cusp.pipeline.run_pipeline", side_effect=fake_pipeline),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            from cusp.pipeline import run_with_reconnect

            await run_with_reconnect(config)
            assert call_count == 2

    async def test_no_retry_when_disabled(self):
        config = CuspConfig(auto_reconnect=False)
        with patch(
            "cusp.pipeline.run_pipeline",
            new_callable=AsyncMock,
            side_effect=ConnectionError("lost"),
        ):
            from cusp.pipeline import run_with_reconnect

            with pytest.raises(ConnectionError):
                await run_with_reconnect(config)

    async def test_retries_on_generic_exception(self):
        config = CuspConfig(auto_reconnect=True, reconnect_delay=0.0)
        call_count = 0

        async def fake_pipeline(cfg):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("oops")

        with (
            patch("cusp.pipeline.run_pipeline", side_effect=fake_pipeline),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            from cusp.pipeline import run_with_reconnect

            await run_with_reconnect(config)
            assert call_count == 2

    async def test_no_retry_generic_when_disabled(self):
        config = CuspConfig(auto_reconnect=False)
        with patch(
            "cusp.pipeline.run_pipeline",
            new_callable=AsyncMock,
            side_effect=RuntimeError("oops"),
        ):
            from cusp.pipeline import run_with_reconnect

            with pytest.raises(RuntimeError):
                await run_with_reconnect(config)
