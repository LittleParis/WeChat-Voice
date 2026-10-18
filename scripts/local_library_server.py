"""Loopback-only original voice library. No external services or telemetry."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import socket
import threading
from urllib.parse import parse_qs, quote, urlparse

from export_original_voices import SHANGHAI, export
from voice_library import VoiceLibrary
from mobile_package import create_package, PackageCancelled
from desktop_connection import DesktopConnection, discover_sources, inspect_source, load_settings, protect_private


class LibraryServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, address, library, webroot, exportroot, project=None, settings=None):
        self.library, self.webroot = library, Path(webroot).resolve()
        self.exportroot = Path(exportroot).resolve()
        self.token = secrets.token_hex(32)
        self.jobs, self.jobs_lock = {}, threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voice-export")
        self.connection = DesktopConnection(project, self.activate_library, settings) if project else None
        super().__init__(address, LibraryHandler)

    def activate_library(self, library):
        self.library = library

    def start_export(self, filters, mode="files"):
        with self.jobs_lock:
            return self._start_export(filters, mode)

    def _start_export(self, filters, mode="files"):
        if self.connection and self.connection.busy():
            raise ValueError("正在连接或刷新数据，请完成后再导出。")
        library = self.library
        if mode not in {"files", "mobile"}:
            raise ValueError("不支持的导出格式")
        records = library.filtered(filters)
        if not records:
            raise ValueError("当前筛选结果没有语音")
        job_id = secrets.token_hex(12)
        folder = self.exportroot / ("语音导出_" + datetime.now(SHANGHAI).strftime("%Y%m%d_%H%M%S") + "_" + job_id[:6])
        job = {"id": job_id, "state": "queued", "completed": 0, "failed": 0,
               "total": len(records), "output": str(folder), "mode": mode, "cancel": threading.Event()}
        with self.jobs_lock:
            self.jobs[job_id] = job
        voices = [library.by_id[row["id"]][1] for row in records]

        def progress(completed, total, failures):
            with self.jobs_lock:
                job.update(completed=completed, failed=failures)

        def run():
            with self.jobs_lock:
                job["state"] = "running"
            try:
                if mode == "mobile":
                    folder.mkdir(parents=True, exist_ok=False)
                    result = create_package(library, folder / "语音档案.voicepack.zip", records,
                                            progress=progress, should_cancel=job["cancel"].is_set)
                    with self.jobs_lock:
                        job.update(state="done", exported=result["exported"])
                    return
                manifest = export(library.root, folder, voices, library.contacts,
                                  progress=progress, should_cancel=job["cancel"].is_set, verbose=False)
                with self.jobs_lock:
                    job["state"] = "cancelled" if manifest.get("cancelled") else "done"
                    job["exported"] = len(manifest["exported"])
            except PackageCancelled:
                with self.jobs_lock:
                    job.update(state="cancelled", exported=0)
            except Exception as error:
                with self.jobs_lock:
                    job.update(state="error", error="导出未完成：" + type(error).__name__)
        self.executor.submit(run)
        return self.job_status(job_id)

    def job_status(self, job_id):
        with self.jobs_lock:
            if job_id not in self.jobs:
                raise ValueError("导出任务不存在")
            return {key: value for key, value in self.jobs[job_id].items() if key != "cancel"}


class LibraryHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def common_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")

    def respond(self, body, status=200, content_type="application/json; charset=utf-8", cookie=False):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.common_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", f"voice_library={self.server.token}; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def permitted_host(self):
        port = self.server.server_port
        return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def authorized(self, mutation=False):
        if not self.permitted_host():
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}:
            return False
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            token = cookie["voice_library"].value if "voice_library" in cookie else ""
        except Exception:
            return False
        return secrets.compare_digest(token, self.server.token) and (not mutation or secrets.compare_digest(self.headers.get("X-Voice-Token", ""), self.server.token))

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self.permitted_host():
            return self.respond({"error": "仅允许本机访问"}, 403)
        parsed = urlparse(self.path)
        path = parsed.path
        self.library = self.server.library
        try:
            if path == "/":
                html = (self.server.webroot / "index.html").read_text(encoding="utf-8").replace("__VOICE_TOKEN__", self.server.token)
                return self.respond(html, content_type="text/html; charset=utf-8", cookie=True)
            assets = {"/app.js": ("app.js", "text/javascript; charset=utf-8"), "/app.css": ("app.css", "text/css; charset=utf-8"),
                      "/connection.js": ("connection.js", "text/javascript; charset=utf-8")}
            if path in assets:
                name, mime = assets[path]
                return self.respond((self.server.webroot / name).read_bytes(), content_type=mime)
            if not self.authorized():
                return self.respond({"error": "请先打开本机语音页面"}, 403)
            if path == "/api/connection":
                connection = self.server.connection
                return self.respond(connection.status() if connection else {"configured": False, "source": "", "job": None})
            if path == "/api/overview":
                return self.respond(self.library.overview())
            if path == "/api/voices":
                query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
                return self.respond(self.library.page(query))
            if re.fullmatch(r"/api/chats/[0-9a-f]{24}/senders", path):
                return self.respond(self.library.senders(path.split("/")[3]))
            if re.fullmatch(r"/api/jobs/[0-9a-f]{24}", path):
                return self.respond(self.server.job_status(path.rsplit("/", 1)[1]))
            match = re.fullmatch(r"/(media|download)/([0-9a-f]{24})\.mp3", path)
            if match:
                return self.audio(match[2], download=match[1] == "download")
            return self.respond({"error": "页面不存在"}, 404)
        except (ValueError, KeyError) as error:
            return self.respond({"error": str(error)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            return self.respond({"error": "读取失败，请检查本地数据副本"}, 500)

    def do_POST(self):
        if not self.authorized(mutation=True):
            return self.respond({"error": "请求无效，请从本机页面重试"}, 403)
        try:
            self.library = self.server.library
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 16384:
                raise ValueError("请求大小无效")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("请求内容无效")
            path = urlparse(self.path).path
            if path.startswith("/api/connection/"):
                connection = self.server.connection
                if not connection:
                    raise ValueError("当前服务未启用数据源配置。")
                if path == "/api/connection/detect":
                    return self.respond({"locations": discover_sources(connection.status()["source"])})
                if path == "/api/connection/inspect":
                    return self.respond(inspect_source(payload.get("path")))
                if path == "/api/connection/start":
                    with self.server.jobs_lock:
                        if any(job["state"] in {"queued", "running"} for job in self.server.jobs.values()):
                            raise ValueError("请先完成或取消当前导出，再连接或刷新数据。")
                        result = connection.start(payload.get("path"), payload.get("account"))
                    return self.respond(result, 202)
                if path == "/api/connection/cancel":
                    return self.respond(connection.cancel())
            if path == "/api/prepare":
                voice_id = payload.get("id", "")
                self.library.prepare(voice_id)
                return self.respond({"id": voice_id, "url": f"/media/{voice_id}.mp3", "download": f"/download/{voice_id}.mp3"})
            if path == "/api/export":
                filters = {key: str(payload.get(key, "")) for key in ("chat", "sender", "since", "until")}
                filters["sort"] = str(payload.get("sort", "desc"))
                return self.respond(self.server.start_export(filters, str(payload.get("mode", "files"))), 202)
            if path == "/api/cancel":
                job_id = payload.get("id", "")
                self.server.job_status(job_id)
                self.server.jobs[job_id]["cancel"].set()
                return self.respond({"ok": True})
            if path == "/api/open-folder":
                job = self.server.job_status(payload.get("id", ""))
                folder = Path(job["output"]).resolve()
                if job["state"] not in {"done", "cancelled"} or not folder.is_relative_to(self.server.exportroot) or not folder.is_dir():
                    raise ValueError("导出文件夹还未准备好")
                if os.name != "nt":
                    raise ValueError("当前系统请根据保存路径手动打开文件夹")
                os.startfile(str(folder))
                return self.respond({"ok": True})
            return self.respond({"error": "操作不存在"}, 404)
        except (ValueError, KeyError, TypeError) as error:
            return self.respond({"error": str(error)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            return self.respond({"error": "操作失败，原始数据未改动，请稍后重试"}, 500)

    def audio(self, voice_id, download=False):
        path = self.library.audio_path(voice_id)
        if not path.exists():
            return self.respond({"error": "音频尚未准备好，请先点击试听"}, 404)
        size = path.stat().st_size
        start, end = 0, size - 1
        requested = self.headers.get("Range")
        if requested:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
            if not match or not any(match.groups()):
                return self.range_error(size)
            if match[1]:
                start = int(match[1])
                end = min(size - 1, int(match[2])) if match[2] else size - 1
            else:
                suffix = int(match[2])
                if suffix == 0:
                    return self.range_error(size)
                start = max(0, size - suffix)
            if start > end or start >= size:
                return self.range_error(size)
        self.send_response(206 if requested else 200)
        self.common_headers()
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if requested:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if download:
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(self.library.download_name(voice_id)))
        self.end_headers()
        if self.command == "HEAD":
            return
        with path.open("rb") as audio:
            audio.seek(start)
            remaining = end - start + 1
            while remaining:
                data = audio.read(min(65536, remaining))
                if not data:
                    break
                self.wfile.write(data)
                remaining -= len(data)

    def range_error(self, size):
        self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
        self.common_headers()
        self.send_header("Content-Range", f"bytes */{size}")
        self.send_header("Content-Length", "0")
        self.end_headers()


def main():
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Optional existing decrypted snapshot; otherwise use saved connection")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--refresh", action="store_true", help="Refresh the saved source after starting the local UI")
    args = parser.parse_args()
    protect_private(project)
    settings = load_settings(project) if not args.input else None
    root = args.input or (project / ".private" / settings["snapshot"] if settings else project / ".private/decrypted")
    cache = project / ".private" / settings["cache"] if settings else project / ".private/library-audio"
    library = VoiceLibrary(root, cache, allow_empty=True)
    server = LibraryServer(("127.0.0.1", args.port), library, project / "web", project / "exports", project, settings)
    if args.refresh and settings:
        server.connection.start(str(Path(settings["source"]).parent.parent), settings["account_id"])
    print(f"READY http://127.0.0.1:{server.server_port} | {len(library.records)} voices, {len(library.chats)} chats", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.connection.close()
        with server.jobs_lock:
            for job in server.jobs.values():
                job["cancel"].set()
        server.server_close()
        server.executor.shutdown(wait=True)


if __name__ == "__main__":
    main()
