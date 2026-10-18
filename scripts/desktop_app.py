"""Double-click Windows entry point; no shell, Python installation or Git required."""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlparse
from urllib.request import ProxyHandler, build_opener
import webbrowser

from runtime_paths import attach_worker_streams, default_data_dir, resources, system_dll_search, WORKERS

VERSION = "0.4.0"


class InstanceLock:
    def __init__(self, path):
        self.file = Path(path).open("a+b")
        self.file.seek(0, 2)
        if not self.file.tell():
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        self.acquired = False

    def acquire(self):
        import msvcrt
        try:
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            self.acquired = True
        except OSError:
            pass
        return self.acquired

    def close(self):
        if self.acquired:
            import msvcrt
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        self.file.close()


def existing_url(data_dir):
    """Only reuse our live instance, never an arbitrary service on a familiar port."""
    try:
        saved = json.loads((data_dir / ".private/desktop-instance.json").read_text(encoding="utf-8"))
        address = urlparse(saved["url"])
        if address.scheme != "http" or address.hostname != "127.0.0.1" or address.path or address.username:
            return None
        with build_opener(ProxyHandler({})).open(saved["url"] + "/api/health", timeout=1) as response:
            health = json.load(response)
        if health.get("app") == "voicekeep" and health.get("instance") == saved["instance"]:
            return saved["url"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def open_browser(url):
    with system_dll_search():
        return webbrowser.open(url)


def create_server(data_dir, on_exit, ports=None):
    from desktop_connection import load_settings
    from local_library_server import LibraryServer
    from voice_library import VoiceLibrary
    settings = load_settings(data_dir)
    root = data_dir / ".private" / settings["snapshot"] if settings else data_dir / ".private/decrypted"
    cache = data_dir / ".private" / settings["cache"] if settings else data_dir / ".private/library-audio"
    library = VoiceLibrary(root, cache, allow_empty=True)
    for port in (ports if ports is not None else [*range(8766, 8786), 0]):
        try:
            return LibraryServer(("127.0.0.1", port), library, resources() / "web", data_dir / "exports",
                                 data_dir, settings, on_exit=on_exit)
        except OSError as error:
            if getattr(error, "winerror", None) not in (10048, 10013) and getattr(error, "errno", None) != 98:
                raise
    raise RuntimeError("No available local port")


class DesktopController:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.server = None
        self.url = None
        self.error = None
        self.booted = threading.Event()
        self.stopping = threading.Event()
        self.stopped = threading.Event()
        self.stop_lock = threading.Lock()
        self.activate_requested = threading.Event()

    def boot(self):
        try:
            self.server = create_server(self.data_dir, self.request_stop)
            self.server.on_activate = self.activate_requested.set
            self.url = f"http://127.0.0.1:{self.server.server_port}"
            threading.Thread(target=self.server.serve_forever, name="voicekeep-http", daemon=True).start()
            info = {"url": self.url, "instance": self.server.instance_id, "pid": os.getpid(), "version": VERSION}
            (self.data_dir / ".private/desktop-instance.json").write_text(json.dumps(info), encoding="utf-8")
        except Exception as error:
            self.error = type(error).__name__
        finally:
            self.booted.set()

    def request_stop(self):
        with self.stop_lock:
            if self.stopping.is_set():
                return
            self.stopping.set()
            threading.Thread(target=self.stop, name="voicekeep-exit", daemon=True).start()

    def stop(self):
        self.booted.wait()
        try:
            if self.server:
                self.server.shutdown()
                with self.server.jobs_lock:
                    for job in self.server.jobs.values():
                        job["cancel"].set()
                self.server.connection.close()
                self.server.server_close()
                self.server.executor.shutdown(wait=True)
            (self.data_dir / ".private/desktop-instance.json").unlink(missing_ok=True)
        finally:
            self.stopped.set()


def run_window(controller, launch_browser=False):
    from qt_desktop import run
    return run(controller)


def activate_existing(url):
    from http.cookiejar import CookieJar
    from urllib.request import HTTPCookieProcessor, Request
    import re
    try:
        client = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
        with client.open(url, timeout=2) as response:
            page = response.read().decode('utf-8')
        token = re.search(r'name="voice-token" content="([a-f0-9]+)"', page)[1]
        request = Request(url + '/api/activate', data=b'{}', headers={'Content-Type': 'application/json', 'X-Voice-Token': token})
        with client.open(request, timeout=2) as response:
            return bool(json.load(response).get('ok'))
    except (OSError, ValueError, TypeError):
        return False


def run_worker(arguments):
    attach_worker_streams()
    if not arguments or arguments[0] not in WORKERS:
        return 2
    script = arguments[0]
    sys.argv = [script, *arguments[1:]]
    if script == "capture_desktop_key.py":
        from capture_desktop_key import main
    else:
        from prepare_desktop_data import main
    try:
        return main() or 0
    except Exception as error:
        print("Worker failed:", type(error).__name__, flush=True)
        return 1


def main(arguments=None):
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if arguments and arguments[0] == "--worker":
        return run_worker(arguments[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--headless", action="store_true", help="For automated validation")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--self-test", type=Path, metavar="REPORT", help="Run synthetic bundled dependency checks")
    parser.add_argument("--qt-check", type=Path, metavar="REPORT", help="For native UI and multimedia validation")
    args = parser.parse_args(arguments)
    if args.self_test:
        from desktop_selftest import run
        return run(args.self_test)
    if args.qt_check:
        from qt_smoke import run
        return run(args.qt_check, args.data_dir)
    if os.name != "nt":
        return 2
    from desktop_connection import protect_private
    data_dir = args.data_dir.resolve()
    protect_private(data_dir)
    instance = InstanceLock(data_dir / ".private/desktop.lock")
    if not instance.acquire():
        for _ in range(15):
            url = existing_url(data_dir)
            if url:
                activate_existing(url)
                instance.close()
                return 0
            time.sleep(.2)
        instance.close()
        if not args.headless:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, "语音档案正在启动或退出，请稍候再试。", "语音档案", 0)
        return 1
    controller = DesktopController(data_dir)
    try:
        if args.headless:
            controller.boot()
            if controller.error:
                controller.request_stop()
            while not controller.stopped.wait(.2):
                pass
        else:
            run_window(controller, not args.no_browser)
        return int(bool(controller.error))
    except KeyboardInterrupt:
        return 0
    finally:
        if not controller.booted.is_set():
            controller.booted.set()
        controller.request_stop()
        controller.stopped.wait()
        instance.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        if "--headless" in sys.argv:
            try:
                index = sys.argv.index("--data-dir")
                diagnostic = Path(sys.argv[index + 1]) / ".private/startup-error.json"
                diagnostic.parent.mkdir(parents=True, exist_ok=True)
                import traceback
                site = traceback.extract_tb(error.__traceback__)[-1]
                details = {"error": type(error).__name__, "at": Path(site.filename).name + ":" + str(site.lineno)}
                if isinstance(error, ImportError):
                    details["import_error"] = str(error)
                diagnostic.write_text(json.dumps(details), encoding="utf-8")
            except Exception:
                pass
            raise SystemExit(1)
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, "语音档案无法启动。请确认程序已完整下载，且本机数据目录可写。\n错误类型：" + type(error).__name__, "语音档案", 0x10)
        raise SystemExit(1)
