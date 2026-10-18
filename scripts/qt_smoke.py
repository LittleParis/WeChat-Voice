"""Automated native-window validation for a built EXE, without browser automation."""
from pathlib import Path
import array
import json
import math
import os
import sys
import threading
import time


def run(report, data_dir):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QUrl
    from PySide6.QtMultimedia import QMediaPlayer
    from desktop_app import DesktopController, activate_existing
    from desktop_connection import protect_private
    from qt_desktop import NativeWindow
    import lameenc
    report, data_dir = Path(report).resolve(), Path(data_dir).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    protect_private(data_dir)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    controller = DesktopController(data_dir)
    controller.boot()
    checks = {}
    window = None

    def wait(predicate, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(.01)
        raise AssertionError("Qt readiness timeout")

    try:
        assert not controller.error
        window = NativeWindow(controller, auto_detect=False)
        window.audio_output.setMuted(True)
        window.show()
        wait(lambda: window.ready and not window.scanning)
        checks["qt_widgets_ready"] = True
        checks["voices"] = len(window.library.records)
        assert window.browse_button.isEnabled()
        checks["native_directory_picker_available"] = True
        assert activate_existing(controller.url)
        assert controller.activate_requested.is_set()
        app.processEvents()
        checks["native_single_instance_activation"] = True
        samples = array.array("h", (int(6000 * math.sin(i * 2 * math.pi * 440 / 24000)) for i in range(24000)))
        if sys.byteorder != "little":
            samples.byteswap()
        encoder = lameenc.Encoder()
        encoder.set_in_sample_rate(24000)
        encoder.set_channels(1)
        encoder.set_bit_rate(64)
        audio = report.parent / "qt-synthetic.mp3"
        audio.write_bytes(bytes(encoder.encode(samples.tobytes()) + encoder.flush()))
        window.player.setSource(QUrl.fromLocalFile(str(audio)))
        window.player.play()
        wait(lambda: window.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState and window.player.position() > 120)
        window.player.pause()
        assert window.player.playbackState() == QMediaPlayer.PlaybackState.PausedState
        checks["qt_multimedia_mp3_playback_and_pause"] = True
        window.player.stop()
        if window.rows:
            window.play_selected()
            wait(lambda: window.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState and window.player.position() > 120)
            checks["native_real_voice_playback"] = True
            window.player.stop()
        screenshot = report.with_suffix(".png")
        assert window.grab().save(str(screenshot))
        checks["native_widget_render"] = True
        window.begin_close()
        wait(lambda: controller.stopped.is_set() and not window.futures)
        window.close()
        app.processEvents()
        checks.update(native_window_graceful_exit=True, passed=True)
        code = 0
    except Exception as error:
        import traceback
        site = traceback.extract_tb(error.__traceback__)[-1]
        checks.update(passed=False, error=type(error).__name__, error_at=Path(site.filename).name + ":" + str(site.lineno))
        code = 1
    finally:
        if window:
            window.player.stop()
        controller.request_stop()
        controller.stopped.wait(20)
        if window:
            window.pool.shutdown(wait=True, cancel_futures=True)
        report.write_text(json.dumps(checks, indent=2), encoding="utf-8")
    return code
