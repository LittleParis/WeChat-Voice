import hashlib
import hmac
import io
import json
from pathlib import Path
import shutil
import struct
import sys
import threading
import time
from urllib.error import HTTPError

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import desktop_connection as connection
from desktop_connection import DesktopConnection, inspect_source, load_settings
from local_library_server import LibraryServer
from test_voice_library import library, browser, post


def make_source(root, name="wxid_test"):
    source = root / name / "db_storage"
    (source / "message").mkdir(parents=True)
    (source / "message/message_0.db").write_bytes(b"x" * 4096)
    (source / "message/media_0.db").write_bytes(b"x" * 4096)
    return source


def wait_done(manager):
    deadline = time.monotonic() + 8
    while manager.busy() and time.monotonic() < deadline:
        time.sleep(.01)
    assert not manager.busy()
    return manager.status()["job"]


def test_inspection_accepts_root_account_or_database_and_never_guesses_account(tmp_path):
    root = tmp_path / "数据 目录"
    source = make_source(root)
    first = inspect_source(str(root))["accounts"][0]
    assert first["source"] == str(source.resolve())
    assert inspect_source(str(source.parent))["accounts"] == [first]
    assert inspect_source(str(source))["accounts"] == [first]
    make_source(root, "wxid_second")
    assert len(inspect_source(str(root))["accounts"]) == 2
    manager = DesktopConnection(tmp_path / "project", lambda _: None)
    try:
        with pytest.raises(ValueError, match="请选择"):
            manager.start(str(root), "")
        assert manager.job is None
    finally:
        manager.close()


@pytest.mark.parametrize("value", [None, "", "relative/path", "//server/share", "\\\\server\\share", "a\x00b"])
def test_invalid_paths_rejected_without_filesystem_mutation(tmp_path, value):
    with pytest.raises(ValueError):
        inspect_source(value)
    assert not list(tmp_path.iterdir())


def test_install_or_backup_directory_has_actionable_error(tmp_path):
    with pytest.raises(ValueError, match="Backup"):
        inspect_source(str(tmp_path))


def test_key_reuse_requires_real_page_authentication_and_is_not_in_status(tmp_path):
    source = make_source(tmp_path / "data")
    key = b"0123456789abcdef0123456789abcdef"
    page = bytearray(bytes(range(256)) * 16)
    derived = hashlib.pbkdf2_hmac("sha512", key, page[:16], 256000, 32)
    mac = hashlib.pbkdf2_hmac("sha512", derived, bytes(x ^ 0x3a for x in page[:16]), 2, 32)
    page[4032:] = hmac.new(mac, page[16:4032] + struct.pack("<I", 1), hashlib.sha512).digest()
    (source / "message/message_0.db").write_bytes(page)
    manager = DesktopConnection(tmp_path / "project", lambda _: None)
    manager.private.mkdir(parents=True)
    keyfile = manager.private / "desktop-db-key.txt"
    try:
        keyfile.write_text((b"z" * 32).hex(), encoding="ascii")
        assert manager.reusable_key(source, connection.source_id(source)) is None
        keyfile.write_text(key.hex(), encoding="ascii")
        assert manager.reusable_key(source, connection.source_id(source)) == keyfile
        assert key.hex() not in json.dumps(manager.status())
        page[1000] ^= 1
        (source / "message/message_0.db").write_bytes(page)
        assert manager.reusable_key(source, connection.source_id(source)) is None
    finally:
        manager.close()


@pytest.fixture
def managed(tmp_path, monkeypatch, library):
    project = tmp_path / "project"
    project.mkdir()
    root = tmp_path / "data"
    source = make_source(root)
    (project / ".private").mkdir()
    key = project / ".private/desktop-db-key.txt"
    key.write_text("ab" * 32)
    activated = []
    manager = DesktopConnection(project, activated.append)
    monkeypatch.setattr(connection, "protect_private", lambda _: None)
    monkeypatch.setattr(manager, "reusable_key", lambda *_: key)

    def prepare(script, arguments):
        assert script == "prepare_desktop_data.py"
        destination = Path(arguments[arguments.index("--output") + 1])
        shutil.copytree(library.root, destination)
        (destination / "source-manifest.json").write_text('{"databases":[]}')

    monkeypatch.setattr(manager, "run_child", prepare)
    try:
        yield manager, activated, root, source
    finally:
        manager.close()


def test_success_persists_isolated_snapshot_and_refresh_keeps_previous_files(managed):
    manager, activated, root, source = managed
    account = inspect_source(str(root))["accounts"][0]
    original = (source / "message/media_0.db").read_bytes()
    manager.start(str(root), account["id"])
    job = wait_done(manager)
    assert job["state"] == "done" and job["reused_key"] and job["voice_count"] == 4
    first = activated[-1]
    saved = load_settings(manager.project)
    assert saved["account_id"] == account["id"]
    assert saved["source"] == str(source)
    manager.start(str(root), account["id"])
    assert wait_done(manager)["state"] == "done"
    assert len(activated) == 2 and first.root != activated[-1].root
    assert first.root.exists() and first.cache != activated[-1].cache
    assert (source / "message/media_0.db").read_bytes() == original
    assert "ab" * 32 not in json.dumps(manager.status())


@pytest.mark.parametrize("stage", ["child", "index"])
def test_preparation_failure_preserves_saved_config_and_live_library(managed, monkeypatch, stage):
    manager, activated, root, _ = managed
    account = inspect_source(str(root))["accounts"][0]
    manager.start(str(root), account["id"])
    assert wait_done(manager)["state"] == "done"
    before = (manager.private / "connection.json").read_bytes()
    old = activated[-1]

    def fail(script, arguments):
        if stage == "child":
            raise RuntimeError("secret-candidate-not-for-browser")
        destination = Path(arguments[arguments.index("--output") + 1])
        destination.mkdir()

    monkeypatch.setattr(manager, "run_child", fail)
    manager.start(str(root), account["id"])
    job = wait_done(manager)
    assert job["state"] == "error" and "未更换" in job["message"]
    assert "secret-candidate" not in json.dumps(manager.status())
    assert (manager.private / "connection.json").read_bytes() == before
    assert len(activated) == 1 and old.root.exists()


def test_cancel_and_duplicate_start_leave_no_activation(managed, monkeypatch):
    manager, activated, root, _ = managed
    entered = threading.Event()

    def block(*_):
        entered.set()
        assert manager.cancelled.wait(5)
        manager.check_cancelled()

    monkeypatch.setattr(manager, "run_child", block)
    account = inspect_source(str(root))["accounts"][0]
    manager.start(str(root), account["id"])
    assert entered.wait(3)
    with pytest.raises(ValueError, match="已有"):
        manager.start(str(root), account["id"])
    manager.cancel()
    assert wait_done(manager)["state"] == "cancelled"
    assert not activated and not (manager.private / "connection.json").exists()


def test_capture_child_output_is_allowlisted_not_relayed(managed, monkeypatch):
    manager, _, _, _ = managed
    secret = "c3" * 32

    class Process:
        def __init__(self, *_args, **_kwargs):
            self.stdout = io.StringIO("READY: Exit WeChat\n" + secret + "\nREADY: Reopen WeChat\nDesktop WeChat detected\n")
        def poll(self): return 0
        def wait(self): return 0

    monkeypatch.setattr(connection.subprocess, "Popen", Process)
    manager.job = {"state": "running"}
    DesktopConnection.run_child(manager, "capture_desktop_key.py", [])
    assert manager.job["stage"] == "capturing"
    assert secret not in json.dumps(manager.status())
    manager.job["state"] = "done"


def test_settings_reject_paths_outside_private_root(tmp_path):
    private = tmp_path / ".private"
    private.mkdir()
    (private / "connection.json").write_text(json.dumps({"snapshot": "../../other", "cache": "audio", "key": "key"}))
    assert load_settings(tmp_path) is None


def test_fresh_key_flow_precedes_decryption_and_never_exposes_key(managed, monkeypatch):
    manager, activated, root, _ = managed
    prepare = manager.run_child
    calls = []
    monkeypatch.setattr(manager, "reusable_key", lambda *_: None)

    def capture_then_prepare(script, arguments):
        calls.append(script)
        if script == "capture_desktop_key.py":
            Path(arguments[arguments.index("--output") + 1]).write_text("d2" * 32)
            manager.update(stage="waiting_start", message="请打开微信")
        else:
            prepare(script, arguments)

    monkeypatch.setattr(manager, "run_child", capture_then_prepare)
    account = inspect_source(str(root))["accounts"][0]
    manager.start(str(root), account["id"])
    assert wait_done(manager)["state"] == "done"
    assert calls == ["capture_desktop_key.py", "prepare_desktop_data.py"]
    assert not manager.job["reused_key"] and len(activated) == 1
    assert "d2" * 32 not in json.dumps(manager.status())


def test_capture_failure_does_not_attempt_decryption(managed, monkeypatch):
    manager, activated, root, _ = managed
    calls = []
    monkeypatch.setattr(manager, "reusable_key", lambda *_: None)

    def failure(script, _):
        calls.append(script)
        raise RuntimeError("not-for-client")

    monkeypatch.setattr(manager, "run_child", failure)
    account = inspect_source(str(root))["accounts"][0]
    manager.start(str(root), account["id"])
    job = wait_done(manager)
    assert job["state"] == "error" and job["stage"] == "key"
    assert "5 分钟" in job["message"] and "not-for-client" not in job["message"]
    assert calls == ["capture_desktop_key.py"] and not activated


def test_empty_install_can_start_and_duplicate_listener_cannot_bind(tmp_path):
    from voice_library import VoiceLibrary
    empty = VoiceLibrary(tmp_path / "missing", tmp_path / "cache", allow_empty=True)
    assert empty.overview()["voice_count"] == 0
    server = LibraryServer(("127.0.0.1", 0), empty, Path(__file__).parents[1] / "web", tmp_path / "exports")
    try:
        with pytest.raises(OSError):
            LibraryServer(server.server_address, empty, server.webroot, server.exportroot)
    finally:
        server.server_close()
        server.executor.shutdown(wait=True)


def test_connection_http_mutations_require_csrf_and_invalid_selection_does_not_start(managed, library):
    manager, activated, root, _ = managed
    project = Path(__file__).parents[1]
    server = LibraryServer(("127.0.0.1", 0), library, project / "web", manager.project / "exports")
    server.connection = manager
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        opener, token = browser(base)
        with pytest.raises(HTTPError) as failure:
            post(opener, base, "/api/connection/start", {"path": str(root)}, "wrong")
        assert failure.value.code == 403 and manager.job is None
        inspected = post(opener, base, "/api/connection/inspect", {"path": str(root)}, token)
        with pytest.raises(HTTPError) as failure:
            post(opener, base, "/api/connection/start", {"path": str(root), "account": "wrong"}, token)
        assert failure.value.code == 400 and manager.job is None
        account = inspected["accounts"][0]["id"]
        post(opener, base, "/api/connection/start", {"path": str(root), "account": account}, token)
        assert wait_done(manager)["state"] == "done" and len(activated) == 1
        data = json.load(opener.open(base + "/api/connection"))
        assert data["configured"] and "key" not in data and "key" not in data["job"]
    finally:
        server.shutdown()
        server.server_close()
        server.executor.shutdown(wait=True)
