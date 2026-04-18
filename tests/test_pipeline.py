import asyncio
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cusp.config import CuspConfig
from cusp.pipeline import GroupStreamingSession, StreamingSession, _wav_header


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


def _mock_atv(stream_file=None):
    atv = MagicMock()
    atv.stream.stream_file = stream_file or AsyncMock()
    atv.close.return_value = set()
    return atv


def _mock_target(name: str):
    return SimpleNamespace(name=name, address="10.0.0.1")


class TestGroupStreamingSession:
    @pytest.fixture
    def connect_results(self, monkeypatch):
        """Patch `connect_target` to return/raise from a caller-supplied list."""

        def _install(results):
            queue = list(results)

            async def fake_connect(target):
                nxt = queue.pop(0)
                if isinstance(nxt, BaseException):
                    raise nxt
                return nxt

            monkeypatch.setattr("cusp.pipeline.connect_target", fake_connect)

        return _install

    async def test_start_connects_all_devices(self, connect_results):
        atv_a = _mock_atv()
        atv_b = _mock_atv()
        connect_results([atv_a, atv_b])
        config = CuspConfig(sample_rate=48000, channels=2)

        session = await GroupStreamingSession.start(
            [_mock_target("A"), _mock_target("B")], config
        )
        await asyncio.sleep(0)

        assert len(session._sessions) == 2
        atv_a.stream.stream_file.assert_called_once()
        atv_b.stream.stream_file.assert_called_once()
        await session.stop()

    async def test_start_partial_connect_failure_drops_follower(
        self, connect_results, caplog
    ):
        atv_a = _mock_atv()
        connect_results([atv_a, ConnectionError("B offline")])
        config = CuspConfig(sample_rate=48000, channels=2)

        with caplog.at_level("WARNING", logger="cusp.pipeline"):
            session = await GroupStreamingSession.start(
                [_mock_target("A"), _mock_target("B")], config
            )

        assert len(session._sessions) == 1
        assert session._sessions[0].name == "A"
        assert any(
            "B" in r.message and "Failed to connect" in r.message
            for r in caplog.records
        )
        await session.stop()

    async def test_start_all_connects_fail_raises(self, connect_results):
        connect_results([ConnectionError("A"), ConnectionError("B")])
        config = CuspConfig(sample_rate=48000, channels=2)

        with pytest.raises(ConnectionError):
            await GroupStreamingSession.start(
                [_mock_target("A"), _mock_target("B")], config
            )

    async def test_feed_fans_out_to_all_live_readers(self, connect_results):
        never_a = asyncio.get_event_loop().create_future()
        never_b = asyncio.get_event_loop().create_future()
        atv_a = _mock_atv(AsyncMock(side_effect=lambda _: never_a))
        atv_b = _mock_atv(AsyncMock(side_effect=lambda _: never_b))
        connect_results([atv_a, atv_b])
        config = CuspConfig(sample_rate=48000, channels=2)

        session = await GroupStreamingSession.start(
            [_mock_target("A"), _mock_target("B")], config
        )
        await asyncio.sleep(0)

        reader_a = atv_a.stream.stream_file.call_args[0][0]
        reader_b = atv_b.stream.stream_file.call_args[0][0]

        chunk = b"\x11\x22" * 100
        session.feed(chunk)

        assert bytes(reader_a._buffer).endswith(chunk)
        assert bytes(reader_b._buffer).endswith(chunk)

        never_a.cancel()
        never_b.cancel()
        await session.stop()

    async def test_feed_skips_failed_sub_session(self, connect_results, caplog):
        never_a = asyncio.get_event_loop().create_future()
        atv_a = _mock_atv(AsyncMock(side_effect=lambda _: never_a))
        # B raises as soon as its consumer task runs.
        atv_b = _mock_atv(AsyncMock(side_effect=ConnectionError("B dropped")))
        connect_results([atv_a, atv_b])
        config = CuspConfig(sample_rate=48000, channels=2)

        session = await GroupStreamingSession.start(
            [_mock_target("A"), _mock_target("B")], config
        )
        # Let the consumer tasks run so B's failure is visible.
        await asyncio.sleep(0.01)

        assert session._sessions[1].failed()
        assert not session.failed()  # A is still alive

        with caplog.at_level("WARNING", logger="cusp.pipeline"):
            session.feed(b"\x00\x01" * 50)
            # Feeding again shouldn't log a second time.
            session.feed(b"\x00\x01" * 50)

        drop_msgs = [r for r in caplog.records if "dropped" in r.message]
        assert len(drop_msgs) == 1
        assert "B" in drop_msgs[0].message

        # A's reader kept receiving chunks.
        reader_a = atv_a.stream.stream_file.call_args[0][0]
        assert bytes(reader_a._buffer).endswith(b"\x00\x01" * 50)

        never_a.cancel()
        await session.stop()

    async def test_failed_only_when_all_sub_sessions_dead(self, connect_results):
        atv_a = _mock_atv(AsyncMock(side_effect=ConnectionError("A gone")))
        atv_b = _mock_atv(AsyncMock(side_effect=ConnectionError("B gone")))
        connect_results([atv_a, atv_b])
        config = CuspConfig(sample_rate=48000, channels=2)

        session = await GroupStreamingSession.start(
            [_mock_target("A"), _mock_target("B")], config
        )
        await asyncio.sleep(0.01)

        assert session.failed()
        exc = session.exception()
        assert isinstance(exc, ConnectionError)
        await session.stop()

    async def test_exception_none_while_any_alive(self, connect_results):
        never_a = asyncio.get_event_loop().create_future()
        atv_a = _mock_atv(AsyncMock(side_effect=lambda _: never_a))
        atv_b = _mock_atv(AsyncMock(side_effect=ConnectionError("B gone")))
        connect_results([atv_a, atv_b])
        config = CuspConfig(sample_rate=48000, channels=2)

        session = await GroupStreamingSession.start(
            [_mock_target("A"), _mock_target("B")], config
        )
        await asyncio.sleep(0.01)

        assert not session.failed()
        assert session.exception() is None

        never_a.cancel()
        await session.stop()

    async def test_stop_closes_every_device(self, connect_results):
        atv_a = _mock_atv()
        atv_b = _mock_atv()
        connect_results([atv_a, atv_b])
        config = CuspConfig(sample_rate=48000, channels=2)

        session = await GroupStreamingSession.start(
            [_mock_target("A"), _mock_target("B")], config
        )
        await session.stop()

        atv_a.close.assert_called_once()
        atv_b.close.assert_called_once()

    async def test_stop_tolerates_already_failed_sub_sessions(self, connect_results):
        atv_a = _mock_atv()
        atv_b = _mock_atv(AsyncMock(side_effect=ConnectionError("B gone")))
        connect_results([atv_a, atv_b])
        config = CuspConfig(sample_rate=48000, channels=2)

        session = await GroupStreamingSession.start(
            [_mock_target("A"), _mock_target("B")], config
        )
        await asyncio.sleep(0.01)
        # Must not raise even though B's consumer already errored.
        await session.stop()
        atv_a.close.assert_called_once()
        atv_b.close.assert_called_once()


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
