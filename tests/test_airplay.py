import sys
from pathlib import Path

import pytest

from cusp import airplay


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
