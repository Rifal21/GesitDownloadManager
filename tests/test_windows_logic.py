"""Windows-specific logic that can be verified on any OS (registry and file-system calls are injected)."""
import json
import os
import sys

import pytest

from gesit import hostinstall
from gesit.core import osutil, util


class FakeRegistry:
    def __init__(self):
        self.keys: dict[str, str] = {}

    def set_default(self, key, value):
        self.keys[key] = value

    def get_default(self, key):
        return self.keys.get(key)

    def delete(self, key):
        return self.keys.pop(key, None) is not None


def test_registry_entries_point_at_manifests_per_browser_family(tmp_path):
    reg = FakeRegistry()
    host = r"C:\Users\me\AppData\Local\Programs\Gesit\gesit-native-host.exe"
    names = hostinstall.setup_windows(host, str(tmp_path), reg, extra_ids=["storeidstoreidstoreidstoreid"])
    assert {"Google Chrome", "Microsoft Edge", "Brave", "Firefox"} <= set(names)

    chrome_key = r"Software\Google\Chrome\NativeMessagingHosts\app.gesit.host"
    firefox_key = r"Software\Mozilla\NativeMessagingHosts\app.gesit.host"
    assert reg.keys[r"Software\Microsoft\Edge\NativeMessagingHosts\app.gesit.host"] == reg.keys[chrome_key]
    assert reg.keys[chrome_key] != reg.keys[firefox_key], "Firefox needs its own manifest (allowed_extensions)"

    chrome = json.load(open(reg.keys[chrome_key]))
    assert chrome["path"] == host and chrome["type"] == "stdio" and chrome["name"] == "app.gesit.host"
    assert chrome["allowed_origins"][0] == f"chrome-extension://{hostinstall.DEV_EXTENSION_ID}/"
    assert chrome["allowed_origins"][-1].startswith("chrome-extension://storeid")
    firefox = json.load(open(reg.keys[firefox_key]))
    assert firefox["allowed_extensions"] == ["gesit@gesit.app"] and "allowed_origins" not in firefox


def test_uninstall_removes_keys_and_manifests(tmp_path):
    reg = FakeRegistry()
    hostinstall.setup_windows("C:\\x\\h.exe", str(tmp_path), reg)
    assert os.listdir(tmp_path)
    removed = hostinstall.uninstall_windows(str(tmp_path), reg)
    assert "Google Chrome" in removed and not reg.keys and not os.listdir(tmp_path)
    assert hostinstall.uninstall_windows(str(tmp_path), reg) == [], "second uninstall is a no-op"


def test_is_integrated_reads_the_registry(monkeypatch, tmp_path):
    reg = FakeRegistry()
    monkeypatch.setattr(hostinstall, "_registry", lambda: reg)
    monkeypatch.setattr(sys, "platform", "win32")
    assert hostinstall.is_integrated() is False
    hostinstall.setup_windows("C:\\x\\h.exe", str(tmp_path), reg)
    assert hostinstall.is_integrated() is True


@pytest.mark.parametrize("raw, expected", [
    ("CON", "_CON"), ("con.txt", "_con.txt"), ("NUL.tar.gz", "_NUL.tar.gz"), ("COM1", "_COM1"), ("lpt9.log", "_lpt9.log"),
    ("report. ", "report"), ("a<b>c:d|e?.zip", "a_b_c_d_e_.zip"), ("normal name.pdf", "normal name.pdf"),
    ("console.txt", "console.txt"), ("com10.txt", "com10.txt"),
])
def test_windows_filename_rules(raw, expected):
    assert util.sanitize_filename(raw, windows=True) == expected


def test_linux_keeps_names_that_are_only_special_on_windows():
    assert util.sanitize_filename("con.txt", windows=False) == "con.txt"


def test_bundled_tools_are_found_before_path(monkeypatch, tmp_path):
    monkeypatch.setattr(osutil, "WINDOWS", True)
    (tmp_path / "ffmpeg.exe").write_bytes(b"MZ")
    monkeypatch.setenv("GESIT_TOOLS_DIR", str(tmp_path))
    assert osutil.tool_path("ffmpeg") == str(tmp_path / "ffmpeg.exe")


def test_explorer_reveal_command(monkeypatch, tmp_path):
    f = tmp_path / "a.zip"
    f.write_bytes(b"x")
    monkeypatch.setattr(osutil, "WINDOWS", True)
    assert osutil.reveal_command(str(f)) == ["explorer", f"/select,{os.path.normpath(str(f))}"]
    assert osutil.reveal_command(str(tmp_path / "missing.zip")) is None


def test_windows_power_commands(monkeypatch):
    calls = []
    monkeypatch.setattr(osutil, "WINDOWS", True)
    monkeypatch.setattr(osutil.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    osutil.power_action("poweroff", delay_s=30)
    osutil.power_action("suspend")
    assert calls[0] == ["shutdown", "/s", "/t", "30"]
    assert calls[1][0] == "rundll32.exe" and "SetSuspendState" in calls[1][1]


def test_paths_windows_layout(monkeypatch):
    from gesit.core import paths

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", r"C:\Users\me\AppData\Roaming")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\me\AppData\Local")
    assert paths.config_dir().endswith("Gesit") and "Roaming" in paths.config_dir()
    assert paths.data_dir().endswith("Gesit") and "Local" in paths.data_dir()
