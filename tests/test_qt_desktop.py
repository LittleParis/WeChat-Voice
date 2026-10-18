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
    result.setStyle("Fusion")
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
    wait(app, lambda: widget.ready and not widget.filtering)
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
    wait_filters(window)
    return group


def wait_filters(window):
    wait(QApplication.instance(), lambda: not window.filtering)


def test_native_filters_same_name_senders_and_inclusive_dates(window):
    select_group(window)
    assert len(window.rows) == 3 and window.table.rowCount() == 3
    names = [window.senders.itemText(index) for index in range(window.senders.count())]
    assert len([name for name in names if "同名成员" in name]) == 2
    assert len(set(names)) == len(names)
    window.since.setDate(QDate(2026, 10, 1)); window.until.setDate(QDate(2026, 10, 1))
    window.since_enabled.setChecked(True); window.until_enabled.setChecked(True)
    wait_filters(window)
    assert len(window.rows) == 2
    window.sort.setCurrentIndex(1)
    wait_filters(window)
    assert window.rows[0]["time"] == "00:00:00" and window.rows[1]["time"] == "23:59:59"
    window.senders.setCurrentIndex(window.senders.findData(opaque("sender:alice")))
    wait_filters(window)
    assert len(window.rows) == 1 and window.export_button.text() == "导出当前 1 条"
    window.clear_filters()
    wait_filters(window)
    assert len(window.rows) == 3
    window.search.setText("不存在的会话")
    wait(QApplication.instance(), lambda: window.chat_list.count() == 0)
    assert window.chat_list.count() == 0


def test_invalid_dates_disable_export_instead_of_using_old_results(window):
    select_group(window)
    window.since.setDate(QDate(2026, 10, 3)); window.until.setDate(QDate(2026, 10, 1))
    window.since_enabled.setChecked(True); window.until_enabled.setChecked(True)
    wait_filters(window)
    assert not window.rows and not window.export_button.isEnabled()
    assert "开始日期不能晚于" in window.result_summary.text()


def test_native_export_uses_full_filter_scope_and_creates_mobile_package(app, window):
    group = select_group(window)
    window.senders.setCurrentIndex(window.senders.findData(opaque("sender:alice")))
    wait_filters(window)
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
    assert window.selected_voice()["id"] == window.playing_id


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


def test_slow_filters_keep_ui_responsive_and_only_latest_query_is_applied(app, window, monkeypatch):
    from PySide6.QtCore import QTimer
    select_group(window)
    original = window.library.filtered
    started, release = threading.Event(), threading.Event()
    calls, paints, beats = [], [], []
    gui_thread = threading.get_ident()

    def delayed(filters):
        assert threading.get_ident() != gui_thread
        calls.append(dict(filters))
        if len(calls) == 1:
            started.set()
            assert release.wait(4)
        return original(filters)

    monkeypatch.setattr(window.library, "filtered", delayed)
    heartbeat = QTimer()
    heartbeat.timeout.connect(lambda: beats.append(1))
    heartbeat.start(5)
    window.table.model().modelReset.connect(lambda: paints.append(list(window.table.model().rows)))
    try:
        window.sort.setCurrentIndex(1)
        wait(app, started.is_set)
        window.senders.setCurrentIndex(window.senders.findData(opaque("sender:alice")))
        window.sort.setCurrentIndex(0)
        window.apply_filters()
        assert window.filtering and not window.export_button.isEnabled()
        previous_jobs = len(window.controller.server.jobs)
        window.start_export("files")
        assert len(window.controller.server.jobs) == previous_jobs
        wait(app, lambda: len(beats) >= 5)
    finally:
        release.set()
        heartbeat.stop()
    wait_filters(window)
    assert len(calls) == 2 and len(paints) == 1
    assert len(window.rows) == 1 and window.rows[0]["sender_id"] == opaque("sender:alice")
    assert window.export_button.isEnabled()


def test_fast_chat_switch_never_applies_previous_chat_senders(app, window, monkeypatch):
    select_group(window)
    original = window.library.filtered
    started, release = threading.Event(), threading.Event()
    first = True

    def delayed(filters):
        nonlocal first
        if first:
            first = False
            started.set()
            assert release.wait(4)
        return original(filters)

    monkeypatch.setattr(window.library, "filtered", delayed)
    window.apply_filters()
    wait(app, started.is_set)
    try:
        contact = next(c for c in window.chats if c["kind"] == "contact")
        for index in range(window.chat_list.count()):
            if window.chat_list.item(index).data(Qt.ItemDataRole.UserRole) == contact["id"]:
                window.chat_list.setCurrentRow(index)
                break
    finally:
        release.set()
    wait_filters(window)
    assert window.chat_id == contact["id"]
    assert all(r["chat_id"] == contact["id"] for r in window.rows)
    assert window.senders.count() == 2 and "好友" in window.senders.itemText(1)


def test_preparing_feedback_and_late_audio_result_cannot_replace_new_selection(app, window, monkeypatch):
    select_group(window)
    first, latest = window.rows[:2]
    original = window.library.prepare
    started, release = threading.Event(), threading.Event()

    def delayed(voice_id):
        if voice_id == first["id"]:
            started.set()
            assert release.wait(4)
        return original(voice_id)

    monkeypatch.setattr(window.library, "prepare", delayed)
    window.prepare_play(first)
    assert window.play_button.text() == "准备中…" and not window.play_button.isEnabled()
    wait(app, started.is_set)
    try:
        window.prepare_play(latest)
        wait(app, lambda: window.playing_id == latest["id"])
    finally:
        release.set()
    wait(app, lambda: not any(not f.done() for f in window.futures))
    app.processEvents()
    assert window.playing_id == latest["id"] and window.preparing_id is None
    assert "正在准备原始语音" not in window.notice.text()


def test_model_limits_rendering_to_page_and_preserves_selected_identity(window):
    from qt_components import VoiceModel
    select_group(window)
    base = window.rows[0]
    window.rows = [{**base, "id": f"synthetic-{index}"} for index in range(20000)]
    window.show_page()
    assert isinstance(window.table.model(), VoiceModel)
    assert window.table.rowCount() == 100
    window.table.selectRow(18)
    selected = window.selected_voice()["id"]
    window.show_page()
    assert window.selected_voice()["id"] == selected
    window.move_page(1)
    assert window.selected_voice()["id"] == "synthetic-100"
    assert window.table.rowCount() == 100
    window.table.model().set_playing("synthetic-100", True)
    assert window.table.model().data(window.table.model().index(0, 0), Qt.ItemDataRole.DecorationRole) is not None


def test_native_wheel_motion_can_be_disabled_without_losing_scrolling(app, window):
    from PySide6.QtCore import QPoint, QPointF, QVariantAnimation
    from PySide6.QtGui import QWheelEvent
    select_group(window)
    base = window.rows[0]
    window.rows = [{**base, "id": f"scroll-{index}"} for index in range(100)]
    window.show_page()
    app.processEvents()
    bar = window.table.verticalScrollBar()
    assert bar.maximum() > 0

    def wheel():
        event = QWheelEvent(QPointF(30, 30), QPointF(30, 30), QPoint(), QPoint(0, -120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(window.table.viewport(), event)

    window.set_reduced_motion(False, persist=False)
    wheel()
    assert window.table.scroll_animation.state() == QVariantAnimation.State.Running
    target = window.table.scroll_target
    wait(app, lambda: window.table.scroll_animation.state() == QVariantAnimation.State.Stopped)
    assert bar.value() == target > 0
    window.set_reduced_motion(True, persist=False)
    wheel()
    assert window.table.scroll_animation.state() == QVariantAnimation.State.Stopped
    assert bar.value() > target


def test_keyboard_playback_and_pause(app, window):
    from PySide6.QtTest import QTest
    select_group(window)
    window.activateWindow()
    window.table.setFocus()
    app.processEvents()
    QTest.keyClick(window.table, Qt.Key.Key_Return)
    wait(app, lambda: window.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
    QTest.keyClick(window.table, Qt.Key.Key_Space)
    assert window.player.playbackState() == QMediaPlayer.PlaybackState.PausedState


@pytest.mark.parametrize("width,height", [(900, 640), (1220, 850), (1600, 1000)])
def test_native_layout_keeps_actions_visible_at_supported_sizes(app, window, tmp_path, width, height):
    window.settings.hide()
    window.resize(width, height)
    app.processEvents()
    assert window.size().width() == width and window.size().height() == height
    assert window.table.viewport().height() >= 100
    for control in (window.export_button, window.save_button, window.mobile_button, window.play_button, window.clock):
        assert control.isVisible() and control.geometry().width() >= control.minimumSizeHint().width()
        position = control.mapTo(window, control.rect().bottomRight())
        assert 0 <= position.x() < window.width() and 0 <= position.y() < window.height()
    assert window.grab().save(str(tmp_path / f"native-{width}.png"))
