import json
import os
import struct
import subprocess
import sys
import textwrap

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_py(code, env_extra, **kw):
    env = dict(os.environ, PYTHONPATH=REPO, **env_extra)
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)], env=env, capture_output=True, text=True, timeout=60, **kw)


def test_migration_copies_old_lidm_data_without_touching_it(tmp_path):
    cfg, data = tmp_path / "cfg", tmp_path / "data"
    (cfg / "lidm").mkdir(parents=True)
    (data / "lidm").mkdir(parents=True)
    (cfg / "lidm" / "config.json").write_text(json.dumps({"connections": 12, "download_root": str(tmp_path / "Downloads" / "LIDM")}))
    (data / "lidm" / "downloads.db").write_bytes(b"SQLite format 3\x00 old")
    env = {"XDG_CONFIG_HOME": str(cfg), "XDG_DATA_HOME": str(data), "XDG_DOWNLOAD_DIR": str(tmp_path / "Downloads")}
    r = run_py("""
        from gesit.core.config import migrate_legacy, Config
        print(migrate_legacy())
        c = Config.load(); print(c.connections, c.download_root.endswith('Gesit'))
        print(migrate_legacy())   # second run must be a no-op
    """, env)
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert "config.json" in lines[0] and "downloads.db" in lines[0]
    assert lines[1] == "12 True"
    assert lines[2] == "[]"
    assert (cfg / "lidm" / "config.json").exists() and (data / "lidm" / "downloads.db").exists()  # old data kept
    assert (data / "gesit" / "downloads.db").read_bytes().endswith(b"old")


def test_extension_ids_are_consistent_everywhere():
    key_lines = open(os.path.join(REPO, "extension", "chrome_key.txt")).read().split()
    from gesit import hostinstall

    assert hostinstall.DEV_EXTENSION_ID == key_lines[1], "chrome_key.txt id and hostinstall.DEV_EXTENSION_ID differ"
    ff = json.load(open(os.path.join(REPO, "extension", "firefox", "manifest.json")))
    assert ff["browser_specific_settings"]["gecko"]["id"] == hostinstall.FIREFOX_EXTENSION_ID
    # the pinned public key really hashes to that id
    import base64, hashlib

    der = base64.b64decode(key_lines[0])
    ident = "".join(chr(ord("a") + int(c, 16)) for c in hashlib.sha256(der).hexdigest()[:32])
    assert ident == hostinstall.DEV_EXTENSION_ID
    # native host name must match what the extension asks for
    assert hostinstall.HOST_NAME in open(os.path.join(REPO, "extension", "shared", "background.js")).read()


def test_setup_writes_manifests_for_installed_browsers(tmp_path):
    home = tmp_path / "home"
    for d in (".config/google-chrome", ".config/BraveSoftware/Brave-Browser", ".mozilla", ".var/app/com.brave.Browser"):
        (home / d).mkdir(parents=True)
    env = {"HOME": str(home), "XDG_DATA_HOME": str(home / "d"), "XDG_RUNTIME_DIR": str(tmp_path / "run"),
           "GESIT_EXT_DIR": os.path.join(REPO, "extension", "dist"), "PATH": "/usr/bin:/bin"}
    os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
    subprocess.run([sys.executable, os.path.join(REPO, "extension", "build.py")], check=True, capture_output=True)
    r = run_py("""
        from gesit import hostinstall
        import json
        res = hostinstall.setup(["extraextensionidextraextensionid"])
        print(json.dumps({k: v for k, v in res.items()}))
    """, env)
    assert r.returncode == 0, r.stderr
    res = json.loads(r.stdout.strip().splitlines()[-1])
    assert sorted(res["browsers"]) == ["Brave", "Firefox", "Google Chrome"]
    assert [n for n, _ in res["flatpak"]] == ["Brave (Flatpak)"]
    chrome = json.load(open(home / ".config/google-chrome/NativeMessagingHosts/app.gesit.host.json"))
    assert chrome["name"] == "app.gesit.host" and chrome["type"] == "stdio"
    assert len(chrome["allowed_origins"]) == 2 and chrome["allowed_origins"][1].startswith("chrome-extension://extra")
    ff = json.load(open(home / ".mozilla/native-messaging-hosts/app.gesit.host.json"))
    assert ff["allowed_extensions"] == ["gesit@gesit.app"]
    for m in (chrome, ff):
        assert os.access(m["path"], os.X_OK)
    flat_host = home / ".var/app/com.brave.Browser/data/gesit/gesit-native-host"
    assert flat_host.read_text().startswith("#!/usr/bin/python3")
    assert (home / ".var/app/com.brave.Browser/data/gesit/extension/chrome/manifest.json").exists()


def test_native_host_flag_does_not_import_qt(tmp_path):
    """--native-host runs once per browser message, so it must not pay for (or require) PySide6."""
    fake = tmp_path / "fake"
    (fake / "PySide6").mkdir(parents=True)
    (fake / "PySide6" / "__init__.py").write_text("raise ImportError('Qt must not be imported by the native host')\n")
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    env = dict(os.environ, PYTHONPATH=f"{fake}:{REPO}", XDG_RUNTIME_DIR=str(run))
    p = subprocess.Popen([sys.executable, "-m", "gesit", "--native-host"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, env=env, cwd=REPO)
    body = b'{"cmd":"ping"}'
    p.stdin.write(struct.pack("=I", len(body)) + body)
    p.stdin.flush()
    (n,) = struct.unpack("=I", p.stdout.read(4))
    reply = json.loads(p.stdout.read(n))
    p.stdin.close()
    p.wait(5)
    assert reply["ok"] is False and reply["running"] is False
    assert b"Qt must not" not in p.stderr.read()
