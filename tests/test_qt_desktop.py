import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import json
import sys
import threading
import time

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaPlayer

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from desktop_app import DesktopController, activate_existing
from local_library_server import LibraryServer
from qt_desktop import NativeWindow, save_audio
from test_voice_library import library
from voice_library import opaque


@pytest.fixture(scope="session")
def app():
    result = QApplication.instance() or QApplication([])
    result.setQuitOnLastWindowClosed(False)
    yield result


def wait(app, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(.01)
    assert predicate(), "Qt condition timed out"


@pytest.fixture
def window(app, library, tmp_path):
    controller = DesktopController(tmp_path)
    server = LibraryServer(("127.0.0.1", 0), library, Path(__file__).parents[1] / "web", tmp_path / "exports", tmp_path,
                           on_exit=controller.request_stop)
    controller.server = server
    controller.url = f"http://127.0.0.1:{server.server_port}"
    server.on_activate = controller.activate_requested.set
    threading.Thread(target=server.serve_forever, daemon=True).start()
    controller.booted.set()
    widget = NativeWindow(controller, auto_detect=False)
    widget.audio_output.setMuted(True)
    widget.show()
    wait(app, lambda: widget.ready)
    try:
        yield widget
    finally:
        widget.begin_close()
        wait(app, lambda: controller.stopped.is_set() and not widget.futures)
        widget.close()
        app.processEvents()


def select_group(window):
    group = next(chat for chat in window.chats if chat["kind"] == "group")
    for index in range(window.chat_list.count()):
        item = window.chat_list.item(index)
        if item.data(Qt.ItemDataRole.UserRole) == group["id"]:
            window.chat_list.setCurrentItem(item)
            break
    return group


def test_native_filters_same_name_senders_and_inclusive_dates(window):
    select_group(window)
    assert len(window.rows) == 3 and window.table.rowCount() == 3
    names = [window.senders.itemText(index) for index in range(window.senders.count())]
    assert len([name for name in names if "同名成员" in name]) == 2
    assert len(set(names)) == len(names)
    window.since.setDate(QDate(2026, 10, 1)); window.until.setDate(QDate(2026, 10, 1))
    window.since_enabled.setChecked(True); window.until_enabled.setChecked(True)
    assert len(window.rows) == 2
    window.sort.setCurrentIndex(1)
    assert window.rows[0]["time"] == "00:00:00" and window.rows[1]["time"] == "23:59:59"
    window.senders.setCurrentIndex(window.senders.findData(opaque("sender:alice")))
    assert len(window.rows) == 1 and window.export_button.text() == "导出当前 1 条"
    window.clear_filters()
    assert len(window.rows) == 3
    window.search.setText("不存在的会话")
    assert window.chat_list.count() == 0


def test_invalid_dates_disable_export_instead_of_using_old_results(window):
    select_group(window)
    window.since.setDate(QDate(2026, 10, 3)); window.until.setDate(QDate(2026, 10, 1))
    window.since_enabled.setChecked(True); window.until_enabled.setChecked(True)
    assert not window.rows and not window.export_button.isEnabled()
    assert "开始日期不能晚于" in window.result_summary.text()


def test_native_export_uses_full_filter_scope_and_creates_mobile_package(app, window):
    group = select_group(window)
    window.senders.setCurrentIndex(window.senders.findData(opaque("sender:alice")))
    window.start_export("files")
    assert not window.export_button.isEnabled()
    wait(app, lambda: window.export_job and window.export_job["state"] == "done")
    first = window.export_job
    manifest = json.loads((Path(first["output"]) / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["exported"]) == 1 and manifest["exported"][0]["sender"] == "alice"
    window.start_export("mobile")
    wait(app, lambda: window.export_job and window.export_job["id"] != first["id"] and window.export_job["state"] == "done")
    assert window.export_job["exported"] == 1
    assert (Path(window.export_job["output"]) / "语音档案.voicepack.zip").is_file()


def test_native_playback_pause_continue_and_automatic_next(app, window):
    select_group(window)
    window.continuous.setChecked(True)
    window.play_selected()
    wait(app, lambda: window.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState and window.player.position() > 100)
    first = window.playing_id
    window.play_selected()
    assert window.player.playbackState() == QMediaPlayer.PlaybackState.PausedState
    paused = window.player.position()
    wait(app, lambda: window.play_button.text() == "继续")
    window.play_selected()
    wait(app, lambda: window.player.position() > paused)
    wait(app, lambda: window.playing_id != first and window.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)


def test_save_mp3_uses_original_decoder_and_unicode_destination(window, tmp_path):
    import miniaudio
    select_group(window)
    voice = window.rows[0]
    target = tmp_path / "中文 文件夹/本人样例.mp3"
    saved = save_audio(window.library, voice["id"], target)
    decoded = miniaudio.decode(saved.read_bytes(), nchannels=1, sample_rate=24000)
    assert len(decoded.samples) > 0 and max(decoded.samples) > 0
    assert saved.read_bytes() == window.library.prepare(voice["id"]).read_bytes()


def test_native_path_inspection_error_keeps_current_library(app, window, tmp_path):
    before = window.library
    source = tmp_path / "source/wxid_example/db_storage"
    import shutil
    shutil.copytree(before.root, source)
    window.source_path.setText(str(source.parent.parent))
    window.inspect()
    wait(app, lambda: bool(window.checked_path))
    assert window.connect_button.isEnabled() and window.accounts.currentData()
    window.source_path.setText(str(tmp_path / "missing"))
    assert not window.connect_button.isEnabled()
    window.inspect()
    wait(app, lambda: not window.scanning)
    assert "目录不存在" in window.source_message.text()
    assert window.library is before and before.overview()["voice_count"] == 4


def test_duplicate_start_requests_native_focus_without_browser(window, monkeypatch):
    import desktop_app
    monkeypatch.setattr(desktop_app, "open_browser", lambda *_: pytest.fail("Native mode opened browser"))
    assert activate_existing(window.controller.url)
    assert window.controller.activate_requested.is_set()


def test_qt_window_renders_without_clipping_essential_actions(app, window, tmp_path):
    window.settings.show()
    window.resize(1180, 820)
    app.processEvents()
    assert window.save_button.geometry().width() >= window.save_button.minimumSizeHint().width()
    assert window.connect_button.geometry().width() >= window.connect_button.minimumSizeHint().width()
    assert window.table.height() >= 100
    assert window.grab().save(str(tmp_path / "native-ui.png"))
