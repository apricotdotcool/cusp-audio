import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pyatv.const import Protocol

from cusp import airplay
from cusp.config import CuspConfig


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_save_credentials_writes_0600(monkeypatch, tmp_path):
    creds_path = tmp_path / "cusp" / "credentials.json"
    monkeypatch.setattr(airplay, "CREDENTIALS_PATH", creds_path)

    airplay._save_credentials({"abc": {"name": "Speaker", "raop": "token"}})

    assert creds_path.is_file()
    assert (creds_path.stat().st_mode & 0o777) == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_save_credentials_creates_parent_dir_0700(monkeypatch, tmp_path):
    creds_path = tmp_path / "cusp" / "credentials.json"
    monkeypatch.setattr(airplay, "CREDENTIALS_PATH", creds_path)

    airplay._save_credentials({})

    assert creds_path.parent.is_dir()
    assert (creds_path.parent.stat().st_mode & 0o777) == 0o700


def test_save_credentials_roundtrip(monkeypatch, tmp_path):
    creds_path: Path = tmp_path / "credentials.json"
    monkeypatch.setattr(airplay, "CREDENTIALS_PATH", creds_path)

    data = {"id-1": {"name": "Kitchen", "raop": "secret"}}
    airplay._save_credentials(data)

    assert airplay._load_credentials() == data


def _fake_device(name: str, identifier: str | None = None, has_raop: bool = True):
    """Build a scan-result double with the attributes airplay helpers touch."""
    services = []
    if has_raop:
        services.append(
            SimpleNamespace(protocol=Protocol.RAOP, credentials=None, password=None)
        )
    return SimpleNamespace(
        name=name,
        identifier=identifier or name.lower().replace(" ", "-"),
        services=services,
    )


@pytest.fixture
def fake_scan(monkeypatch):
    """Patch pyatv.scan to return a caller-supplied device list."""

    def _install(devices):
        monkeypatch.setattr(airplay.pyatv, "scan", AsyncMock(return_value=devices))

    return _install


class TestResolveTargets:
    async def test_leader_only(self, fake_scan, monkeypatch):
        monkeypatch.setattr(airplay, "_load_credentials", lambda: {})
        fake_scan([_fake_device("Kitchen"), _fake_device("Office")])
        config = CuspConfig(airplay_target=["Kitchen"])

        resolved = await airplay.resolve_targets(config)

        assert [d.name for d in resolved] == ["Kitchen"]

    async def test_all_resolved_preserves_order(self, fake_scan, monkeypatch):
        monkeypatch.setattr(airplay, "_load_credentials", lambda: {})
        # Scan order deliberately differs from configured order to prove
        # we return the configured order (leader first).
        fake_scan(
            [
                _fake_device("Office"),
                _fake_device("Kitchen"),
                _fake_device("Bedroom"),
            ]
        )
        config = CuspConfig(airplay_target=["Kitchen", "Office", "Bedroom"])

        resolved = await airplay.resolve_targets(config)

        assert [d.name for d in resolved] == ["Kitchen", "Office", "Bedroom"]

    async def test_partial_failure_skips_missing_follower(
        self, fake_scan, monkeypatch, caplog
    ):
        monkeypatch.setattr(airplay, "_load_credentials", lambda: {})
        fake_scan([_fake_device("Kitchen"), _fake_device("Bedroom")])
        config = CuspConfig(airplay_target=["Kitchen", "Office", "Bedroom"])

        with caplog.at_level("WARNING", logger=airplay.logger.name):
            resolved = await airplay.resolve_targets(config)

        assert [d.name for d in resolved] == ["Kitchen", "Bedroom"]
        assert any(
            "Office" in r.message and "not found" in r.message for r in caplog.records
        )

    async def test_all_missing_raises(self, fake_scan, monkeypatch):
        monkeypatch.setattr(airplay, "_load_credentials", lambda: {})
        fake_scan([_fake_device("Bedroom")])
        config = CuspConfig(airplay_target=["Kitchen", "Office"])

        with pytest.raises(ConnectionError) as excinfo:
            await airplay.resolve_targets(config)

        # All missed names are surfaced in the error for easier debugging.
        assert "Kitchen" in str(excinfo.value)
        assert "Office" in str(excinfo.value)

    async def test_empty_config_raises(self, fake_scan, monkeypatch):
        monkeypatch.setattr(airplay, "_load_credentials", lambda: {})
        fake_scan([_fake_device("Kitchen")])
        config = CuspConfig(airplay_target=None)

        with pytest.raises(ConnectionError):
            await airplay.resolve_targets(config)

    async def test_ambiguous_leader_raises(self, fake_scan, monkeypatch):
        monkeypatch.setattr(airplay, "_load_credentials", lambda: {})
        fake_scan([_fake_device("Kitchen", "a"), _fake_device("Kitchen", "b")])
        config = CuspConfig(airplay_target=["Kitchen"])

        with pytest.raises(ValueError, match="Ambiguous"):
            await airplay.resolve_targets(config)

    async def test_ambiguous_follower_is_skipped(self, fake_scan, monkeypatch, caplog):
        monkeypatch.setattr(airplay, "_load_credentials", lambda: {})
        fake_scan(
            [
                _fake_device("Kitchen"),
                _fake_device("Office", "a"),
                _fake_device("Office", "b"),
            ]
        )
        config = CuspConfig(airplay_target=["Kitchen", "Office"])

        with caplog.at_level("WARNING", logger=airplay.logger.name):
            resolved = await airplay.resolve_targets(config)

        assert [d.name for d in resolved] == ["Kitchen"]
        assert any(
            "Office" in r.message and "ambiguous" in r.message for r in caplog.records
        )

    async def test_applies_stored_credentials_and_password(
        self, fake_scan, monkeypatch
    ):
        monkeypatch.setattr(
            airplay,
            "_load_credentials",
            lambda: {"kitchen": {"name": "Kitchen", "raop": "token-k"}},
        )
        fake_scan([_fake_device("Kitchen"), _fake_device("Office")])
        config = CuspConfig(airplay_target=["Kitchen", "Office"], airplay_password="pw")

        resolved = await airplay.resolve_targets(config)

        kitchen_svc = next(
            s for s in resolved[0].services if s.protocol == Protocol.RAOP
        )
        office_svc = next(
            s for s in resolved[1].services if s.protocol == Protocol.RAOP
        )
        assert kitchen_svc.credentials == "token-k"
        assert kitchen_svc.password == "pw"
        # No creds stored for Office — only password applied.
        assert office_svc.credentials is None
        assert office_svc.password == "pw"


class TestResolveTargetWrapper:
    async def test_returns_leader(self, fake_scan, monkeypatch):
        monkeypatch.setattr(airplay, "_load_credentials", lambda: {})
        fake_scan([_fake_device("Kitchen"), _fake_device("Office")])
        config = CuspConfig(airplay_target=["Kitchen", "Office"])

        target = await airplay.resolve_target(config)

        assert target.name == "Kitchen"
