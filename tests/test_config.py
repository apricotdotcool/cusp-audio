from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from cusp.cli import main
from cusp.config import CuspConfig, _find_config_file, load_config


class TestCuspConfigDefaults:
    def test_defaults(self):
        cfg = CuspConfig()
        assert cfg.audio_device is None
        assert cfg.sample_rate is None
        assert cfg.channels is None
        assert cfg.blocksize == 1024
        assert cfg.airplay_target is None
        assert cfg.airplay_password is None
        assert cfg.auto_reconnect is True
        assert cfg.reconnect_delay == 5.0
        assert cfg.silence_threshold == 0.01
        assert cfg.idle_timeout == 30.0
        assert cfg.target_refresh_interval == 300.0
        assert cfg.log_level == "INFO"
        assert cfg.log_file is None

    def test_custom_values(self):
        cfg = CuspConfig(
            audio_device="USB",
            sample_rate=44100,
            channels=1,
            blocksize=512,
            airplay_target=["Kitchen"],
            airplay_password="secret",
            auto_reconnect=False,
            reconnect_delay=10.0,
            silence_threshold=0.05,
            idle_timeout=60.0,
            target_refresh_interval=600.0,
            log_level="DEBUG",
            log_file="/tmp/cusp.log",
        )
        assert cfg.audio_device == "USB"
        assert cfg.sample_rate == 44100
        assert cfg.channels == 1
        assert cfg.blocksize == 512
        assert cfg.airplay_target == ["Kitchen"]
        assert cfg.airplay_password == "secret"
        assert cfg.auto_reconnect is False
        assert cfg.reconnect_delay == 10.0
        assert cfg.silence_threshold == 0.05
        assert cfg.idle_timeout == 60.0
        assert cfg.target_refresh_interval == 600.0
        assert cfg.log_level == "DEBUG"
        assert cfg.log_file == "/tmp/cusp.log"


class TestLoadConfig:
    def test_full_toml(self, tmp_path):
        toml = tmp_path / "cusp.toml"
        toml.write_text(
            '[audio]\ndevice = "USB Audio"\nsample_rate = 44100\n'
            "channels = 1\nblocksize = 512\n\n"
            '[airplay]\ntarget = "Living Room"\npassword = "pw"\n\n'
            "[behavior]\nauto_reconnect = false\nreconnect_delay = 10.0\n"
            "silence_threshold = 0.05\nidle_timeout = 60.0\n"
            'target_refresh_interval = 600.0\nlog_level = "DEBUG"\n'
            'log_file = "/tmp/cusp.log"\n'
        )
        cfg = load_config(config_path=str(toml))
        assert cfg.audio_device == "USB Audio"
        assert cfg.sample_rate == 44100
        assert cfg.channels == 1
        assert cfg.blocksize == 512
        assert cfg.airplay_target == ["Living Room"]
        assert cfg.airplay_password == "pw"
        assert cfg.auto_reconnect is False
        assert cfg.reconnect_delay == 10.0
        assert cfg.silence_threshold == 0.05
        assert cfg.idle_timeout == 60.0
        assert cfg.target_refresh_interval == 600.0
        assert cfg.log_level == "DEBUG"
        assert cfg.log_file == "/tmp/cusp.log"

    def test_partial_toml(self, tmp_path):
        toml = tmp_path / "cusp.toml"
        toml.write_text("[audio]\nsample_rate = 96000\n")
        cfg = load_config(config_path=str(toml))
        assert cfg.sample_rate == 96000
        assert cfg.audio_device is None
        assert cfg.airplay_target is None
        assert cfg.auto_reconnect is True

    def test_device_string_digit_coercion(self, tmp_path):
        toml = tmp_path / "cusp.toml"
        toml.write_text('[audio]\ndevice = "3"\n')
        cfg = load_config(config_path=str(toml))
        assert cfg.audio_device == 3
        assert isinstance(cfg.audio_device, int)

    def test_device_string_name(self, tmp_path):
        toml = tmp_path / "cusp.toml"
        toml.write_text('[audio]\ndevice = "USB Audio"\n')
        cfg = load_config(config_path=str(toml))
        assert cfg.audio_device == "USB Audio"

    def test_device_system(self, tmp_path):
        toml = tmp_path / "cusp.toml"
        toml.write_text('[audio]\ndevice = "system"\n')
        cfg = load_config(config_path=str(toml))
        assert cfg.audio_device == "system"

    def test_device_int(self, tmp_path):
        toml = tmp_path / "cusp.toml"
        toml.write_text("[audio]\ndevice = 5\n")
        cfg = load_config(config_path=str(toml))
        assert cfg.audio_device == 5
        assert isinstance(cfg.audio_device, int)

    def test_cli_overrides_apply(self, tmp_path):
        toml = tmp_path / "cusp.toml"
        toml.write_text("[audio]\nsample_rate = 44100\n")
        cfg = load_config(config_path=str(toml), sample_rate=96000)
        assert cfg.sample_rate == 96000

    def test_cli_overrides_none_ignored(self, tmp_path):
        toml = tmp_path / "cusp.toml"
        toml.write_text("[audio]\nsample_rate = 44100\n")
        cfg = load_config(config_path=str(toml), sample_rate=None)
        assert cfg.sample_rate == 44100

    def test_airplay_target_string_normalized_to_list(self, tmp_path):
        toml = tmp_path / "cusp.toml"
        toml.write_text('[airplay]\ntarget = "Living Room"\n')
        cfg = load_config(config_path=str(toml))
        assert cfg.airplay_target == ["Living Room"]

    def test_airplay_target_string_comma_separated(self, tmp_path):
        toml = tmp_path / "cusp.toml"
        toml.write_text('[airplay]\ntarget = "Living Room, Kitchen, Office"\n')
        cfg = load_config(config_path=str(toml))
        assert cfg.airplay_target == ["Living Room", "Kitchen", "Office"]

    def test_airplay_target_string_comma_separated_drops_empty_entries(self, tmp_path):
        toml = tmp_path / "cusp.toml"
        toml.write_text('[airplay]\ntarget = "Living Room,,  , Kitchen"\n')
        cfg = load_config(config_path=str(toml))
        assert cfg.airplay_target == ["Living Room", "Kitchen"]

    def test_airplay_target_array(self, tmp_path):
        toml = tmp_path / "cusp.toml"
        toml.write_text('[airplay]\ntarget = ["Living Room", "Kitchen", "Office"]\n')
        cfg = load_config(config_path=str(toml))
        assert cfg.airplay_target == ["Living Room", "Kitchen", "Office"]

    def test_airplay_target_array_strips_whitespace(self, tmp_path):
        toml = tmp_path / "cusp.toml"
        toml.write_text('[airplay]\ntarget = ["  Living Room ", "Kitchen"]\n')
        cfg = load_config(config_path=str(toml))
        assert cfg.airplay_target == ["Living Room", "Kitchen"]

    def test_airplay_target_array_drops_empty_entries(self, tmp_path):
        toml = tmp_path / "cusp.toml"
        toml.write_text('[airplay]\ntarget = ["Living Room", "", "   "]\n')
        cfg = load_config(config_path=str(toml))
        assert cfg.airplay_target == ["Living Room"]

    def test_airplay_target_empty_string_becomes_empty_list(self, tmp_path):
        toml = tmp_path / "cusp.toml"
        toml.write_text('[airplay]\ntarget = ""\n')
        cfg = load_config(config_path=str(toml))
        assert cfg.airplay_target == []

    def test_airplay_target_cli_list_overrides_toml(self, tmp_path):
        toml = tmp_path / "cusp.toml"
        toml.write_text('[airplay]\ntarget = "Living Room"\n')
        cfg = load_config(config_path=str(toml), airplay_target=["Kitchen", "Office"])
        assert cfg.airplay_target == ["Kitchen", "Office"]

    def test_no_file_returns_defaults(self):
        cfg = load_config(config_path="/nonexistent/path.toml")
        assert cfg == CuspConfig()

    def test_no_file_none_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        cfg = load_config(config_path=None)
        assert cfg == CuspConfig()


class TestFindConfigFile:
    def test_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "cusp.toml").write_text("")
        assert _find_config_file() == Path("cusp.toml")

    def test_xdg(self, tmp_path, monkeypatch):
        (tmp_path / "empty").mkdir()
        monkeypatch.chdir(tmp_path / "empty")
        config_dir = tmp_path / ".config" / "cusp"
        config_dir.mkdir(parents=True)
        (config_dir / "cusp.toml").write_text("")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        assert _find_config_file() == Path.home() / ".config" / "cusp" / "cusp.toml"

    def test_cwd_takes_precedence(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "cusp.toml").write_text("")
        config_dir = tmp_path / ".config" / "cusp"
        config_dir.mkdir(parents=True)
        (config_dir / "cusp.toml").write_text("")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        assert _find_config_file() == Path("cusp.toml")

    def test_none_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        assert _find_config_file() is None


class TestStreamCLITargets:
    """CLI parsing for `cusp stream -t …`. The stream pipeline is mocked."""

    def _run(self, args, monkeypatch, tmp_path):
        # Keep the CLI from finding a user/cwd config file.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        captured: dict = {}

        async def fake_run_stream(config):
            captured["config"] = config

        with patch("cusp.cli._run_stream", fake_run_stream):
            runner = CliRunner()
            result = runner.invoke(main, ["stream", *args])
        return result, captured

    def test_cli_single_target(self, monkeypatch, tmp_path):
        result, captured = self._run(["-t", "Living Room"], monkeypatch, tmp_path)
        assert result.exit_code == 0
        assert captured["config"].airplay_target == ["Living Room"]

    def test_cli_comma_separated_targets(self, monkeypatch, tmp_path):
        result, captured = self._run(
            ["-t", "Living Room, Kitchen, Office"], monkeypatch, tmp_path
        )
        assert result.exit_code == 0
        assert captured["config"].airplay_target == [
            "Living Room",
            "Kitchen",
            "Office",
        ]

    def test_cli_comma_separated_drops_empty_entries(self, monkeypatch, tmp_path):
        result, captured = self._run(
            ["-t", "Living Room,,  , Kitchen"], monkeypatch, tmp_path
        )
        assert result.exit_code == 0
        assert captured["config"].airplay_target == ["Living Room", "Kitchen"]

    def test_cli_empty_target_rejected(self, monkeypatch, tmp_path):
        result, captured = self._run(["-t", ""], monkeypatch, tmp_path)
        assert result.exit_code == 1
        assert "No AirPlay target specified" in result.output
        assert "config" not in captured

    def test_cli_whitespace_only_target_rejected(self, monkeypatch, tmp_path):
        result, captured = self._run(["-t", "  ,  ,  "], monkeypatch, tmp_path)
        assert result.exit_code == 1
        assert "No AirPlay target specified" in result.output
        assert "config" not in captured

    def test_cli_target_overrides_toml(self, monkeypatch, tmp_path):
        toml = tmp_path / "cusp.toml"
        toml.write_text('[airplay]\ntarget = ["Living Room", "Kitchen"]\n')
        result, captured = self._run(
            ["-c", str(toml), "-t", "Office"], monkeypatch, tmp_path
        )
        assert result.exit_code == 0
        assert captured["config"].airplay_target == ["Office"]

    def test_cli_no_target_uses_toml_list(self, monkeypatch, tmp_path):
        toml = tmp_path / "cusp.toml"
        toml.write_text('[airplay]\ntarget = ["Living Room", "Kitchen"]\n')
        result, captured = self._run(["-c", str(toml)], monkeypatch, tmp_path)
        assert result.exit_code == 0
        assert captured["config"].airplay_target == ["Living Room", "Kitchen"]
