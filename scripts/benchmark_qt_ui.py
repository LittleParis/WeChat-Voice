"""Developer-only, synthetic Qt page-refresh benchmark; never opens user data."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("build/qt-ui-benchmark.json"))
    parser.add_argument("--baseline-module", type=Path, help="Optional saved qt_desktop.py from an earlier revision")
    args = parser.parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root / "scripts"), str(root / "tests")]
    from PySide6.QtWidgets import QApplication
    from desktop_app import DesktopController
    from local_library_server import LibraryServer
    from test_voice_library import library
    if args.baseline_module:
        spec = importlib.util.spec_from_file_location("qt_baseline", args.baseline_module)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        NativeWindow = module.NativeWindow
    else:
        from qt_desktop import NativeWindow
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory(prefix="voicekeep-ui-benchmark-") as temporary:
        home = Path(temporary)
        lib = library.__wrapped__(home)
        controller = DesktopController(home)
        controller.server = LibraryServer(("127.0.0.1", 0), lib, root / "web", home / "exports", home)
        controller.url = "http://127.0.0.1:" + str(controller.server.server_port)
        threading.Thread(target=controller.server.serve_forever, daemon=True).start()
        controller.booted.set()
        window = NativeWindow(controller, auto_detect=False)
        window.resize(1180, 820)
        window.show()

        def wait(predicate, seconds=8):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                app.processEvents()
                if predicate():
                    return
                time.sleep(.005)
            raise AssertionError("Benchmark readiness timeout")

        try:
            wait(lambda: window.ready and not getattr(window, "filtering", False))
            window.timer.stop()
            window.rows = [{**lib.records[0], "id": f"synthetic-{i}", "sender": f"示例成员 {i}"} for i in range(100)]
            window.show_page()
            app.processEvents()
            samples = []
            for _ in range(50):
                start = time.perf_counter()
                window.show_page()
                app.processEvents()
                samples.append((time.perf_counter() - start) * 1000)
            assert window.table.rowCount() == 100
            report = {"page_rows": 100, "iterations": 50, "window": "1180x820", "platform": "offscreen",
                "page_refresh_median_ms": round(statistics.median(samples), 3),
                "page_refresh_p95_ms": round(sorted(samples)[47], 3)}
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report))
        finally:
            window.begin_close()
            wait(lambda: controller.stopped.is_set() and all(f.done() for f in window.futures), 20)
            window.close()
            window.pool.shutdown(wait=True, cancel_futures=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
