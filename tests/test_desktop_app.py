import json
from pathlib import Path
import sys
from urllib.error import URLError

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import desktop_app
import runtime_paths


def test_worker_commands_do_not_run_a_script_via_the_frozen_exe(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "resources"), raising=False)
    assert runtime_paths.resources() == (tmp_path / "resources").resolve()
    command = runtime_paths.worker_command("prepare_desktop_data.py", ["--source", "path with spaces"])
    assert command == [sys.executable, "--worker", "prepare_desktop_data.py", "--source", "path with spaces"]
    with pytest.raises(ValueError):
        runtime_paths.worker_command("arbitrary.py", [])


def test_frozen_data_is_separate_from_temporary_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "temporary"), raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user"))
    assert runtime_paths.default_data_dir() == tmp_path / "user/VoiceKeep"
    assert not runtime_paths.default_data_dir().is_relative_to(runtime_paths.resources())


def test_single_instance_lock_is_released(tmp_path):
    first = desktop_app.InstanceLock(tmp_path / "instance.lock")
    second = desktop_app.InstanceLock(tmp_path / "instance.lock")
    try:
        assert first.acquire()
        assert not second.acquire()
        first.close()
        assert second.acquire()
    finally:
        if not first.file.closed:
            first.close()
        second.close()


@pytest.mark.parametrize("url", ["https://example.com", "http://example.com:8766", "http://127.0.0.1:8766/untrusted", "http://x@127.0.0.1:8766"])
def test_existing_instance_cannot_open_external_or_unverified_urls(tmp_path, url):
    (tmp_path / ".private").mkdir()
    (tmp_path / ".private/desktop-instance.json").write_text(json.dumps({"url": url, "instance": "x"}))
    assert desktop_app.existing_url(tmp_path) is None


def test_fallback_port_does_not_stop_or_reuse_another_service(tmp_path):
    from voice_library import VoiceLibrary
    from local_library_server import LibraryServer
    from urllib.request import build_opener, ProxyHandler
    library = VoiceLibrary(tmp_path / "empty", tmp_path / "cache", allow_empty=True)
    occupied = LibraryServer(("127.0.0.1", 0), library, Path(__file__).parents[1] / "web", tmp_path / "exports")
    try:
        server = desktop_app.create_server(tmp_path, lambda: None, ports=[occupied.server_port, 0])
        try:
            assert server.server_port != occupied.server_port
            assert server.server_address[0] == "127.0.0.1"
        finally:
            server.connection.close()
            server.server_close()
            server.executor.shutdown(wait=True)
    finally:
        occupied.server_close()
        occupied.executor.shutdown(wait=True)
