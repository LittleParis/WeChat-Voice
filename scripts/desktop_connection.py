"""Local data-source setup. Never return key material or child output to HTTP clients."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading

from capture_desktop_key import authenticate_first_page
from export_original_voices import SHANGHAI
from voice_library import VoiceLibrary


def source_id(source):
    return hashlib.sha256(os.path.normcase(str(source)).encode()).hexdigest()[:24]


def inspect_source(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise ValueError("请填写电脑版微信的聊天数据目录。")
    value = value.strip().strip('"')
    if value.startswith(("\\\\", "//")) or "\x00" in value:
        raise ValueError("请选择这台电脑上的本地目录。")
    root = Path(value).expanduser()
    if not root.is_absolute():
        raise ValueError("请输入完整目录路径，例如 D:\\xwechat_files。")
    try:
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise OSError()
        if root.name == "db_storage":
            candidates = [root.parent]
        elif (root / "db_storage").is_dir():
            candidates = [root]
        else:
            candidates = [item for item in root.iterdir() if item.is_dir() and (item / "db_storage").is_dir()]
        accounts = []
        for account in candidates:
            source = (account / "db_storage").resolve()
            database = source / "message/message_0.db"
            if database.is_file() and any((source / "message").glob("media_*.db")):
                accounts.append({"id": source_id(source), "name": account.name, "source": str(source)})
        if not accounts:
            raise ValueError("没有找到支持的微信 4.x 聊天数据库。请选择 xwechat_files、账号目录或 db_storage；不要选择安装目录或 Backup。")
        return {"path": str(root), "accounts": sorted(accounts, key=lambda row: row["name"])}
    except OSError:
        raise ValueError("目录不存在或无法读取，请在微信设置中确认聊天数据保存位置。") from None


def discover_sources(saved=None):
    home = Path.home()
    candidates = [home / "Documents/xwechat_files", home / "OneDrive/Documents/xwechat_files"]
    if saved:
        candidates.insert(0, Path(saved))
    if os.name == "nt":
        # Only known shallow locations, never recursively scan the user's drives.
        candidates.extend(Path(f"{drive}:/xwechat_files") for drive in "CDEFGHIJKLMNOPQRSTUVWXYZ")
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                documents = os.path.expandvars(winreg.QueryValueEx(key, "Personal")[0])
                candidates.append(Path(documents) / "xwechat_files")
        except OSError:
            pass
    found, seen = [], set()
    for path in candidates:
        try:
            detected = inspect_source(str(path))
            ids = tuple(row["id"] for row in detected["accounts"])
            if ids not in seen:
                found.append(detected)
                seen.add(ids)
        except ValueError:
            continue
    return found


def protect_private(project):
    private = project / ".private"
    if os.name == "nt":
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-File", str(project / "scripts/Protect-PrivateDirectory.ps1"), "-Path", str(private)],
                                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError("private_permissions")
    else:
        private.mkdir(mode=0o700, parents=True, exist_ok=True)
        private.chmod(0o700)


def load_settings(project):
    private = (Path(project) / ".private").resolve()
    try:
        config = json.loads((private / "connection.json").read_text(encoding="utf-8"))
        for field in ("snapshot", "cache", "key"):
            path = (private / config[field]).resolve()
            if not path.is_relative_to(private) or path == private:
                return None
        if not (private / config["snapshot"] / "source-manifest.json").is_file():
            return None
        if source_id(Path(config["source"]).resolve()) != config["account_id"]:
            return None
        return config
    except (OSError, ValueError, KeyError, TypeError):
        return None


class ConnectionCancelled(Exception):
    pass


class DesktopConnection:
    def __init__(self, project, activate, settings=None):
        self.project = Path(project).resolve()
        self.private = self.project / ".private"
        self.activate = activate
        self.settings = settings
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wechat-connect")
        self.job = None
        self.process = None
        self.cancelled = threading.Event()

    def status(self):
        with self.lock:
            config = self.settings
            return {"configured": bool(config), "source": str(Path(config["source"]).parent.parent) if config else "",
                    "account": config["account"] if config else "", "account_id": config["account_id"] if config else "",
                    "updated_at": config["updated_at"] if config else "",
                    "job": dict(self.job) if self.job else None}

    def busy(self):
        with self.lock:
            return self.job is not None and self.job["state"] == "running"

    def update(self, **values):
        with self.lock:
            self.job.update(values)

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise ConnectionCancelled()

    def start(self, path, account_id):
        detected = inspect_source(path)
        selected = next((item for item in detected["accounts"] if item["id"] == account_id), None)
        if selected is None:
            raise ValueError("请选择目录检查结果中的微信账号。")
        with self.lock:
            if self.busy():
                raise ValueError("已有连接任务正在处理，请等待或取消。")
            self.cancelled.clear()
            self.job = {"id": secrets.token_hex(12), "state": "running", "stage": "checking",
                        "message": "正在检查数据目录和本地密钥…", "completed": 0, "total": 0,
                        "account": selected["name"]}
            self.executor.submit(self.run, selected)
            return self.status()

    def cancel(self):
        with self.lock:
            if self.busy():
                self.cancelled.set()
                self.job["message"] = "正在取消，保留当前已加载的语音列表…"
                if self.process and self.process.poll() is None:
                    self.process.terminate()
        return self.status()

    def run_child(self, script, arguments):
        self.check_cancelled()
        command = [sys.executable, "-u", str(self.project / "scripts" / script), *map(str, arguments)]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        with self.lock:
            self.check_cancelled()
            self.process = subprocess.Popen(command, cwd=self.project, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            process = self.process
        # Only allowlisted status markers are interpreted; raw child output is discarded.
        try:
            for line in process.stdout:
                self.check_cancelled()
                if script == "capture_desktop_key.py":
                    if line.startswith("READY: Exit"):
                        self.update(stage="waiting_exit", message="请从电脑右下角微信托盘菜单退出微信，再手动打开并登录。仅关闭窗口不会退出。")
                    elif line.startswith("READY: Reopen"):
                        self.update(stage="waiting_start", message="请手动打开电脑版微信并登录，页面会自动继续。")
                    elif line.startswith("Desktop WeChat detected"):
                        self.update(stage="capturing", message="已检测到微信，正在获取并验证本地数据库密钥…")
                elif line.startswith("Verified:"):
                    with self.lock:
                        self.job["completed"] += 1
            code = process.wait()
            self.check_cancelled()
            if code:
                raise RuntimeError(script)
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait()
            process.stdout.close()
            with self.lock:
                self.process = None

    def reusable_key(self, source, account_id):
        candidates = []
        if self.settings and self.settings["account_id"] == account_id:
            candidates.append(self.private / self.settings["key"])
        candidates.append(self.private / "desktop-db-key.txt")
        keys = (self.private / "connections" / account_id).glob("*/key.txt")
        candidates.extend(sorted(keys, key=lambda path: path.stat().st_mtime, reverse=True)[:20])
        with (source / "message/message_0.db").open("rb") as handle:
            page = handle.read(4096)
        if len(page) != 4096:
            raise ValueError("所选数据库不完整，请确认目录并在微信完成同步后重试。")
        for path in candidates:
            self.check_cancelled()
            try:
                key = bytes.fromhex(path.read_text(encoding="ascii").strip())
                if authenticate_first_page(key, page):
                    return path
            except (OSError, ValueError):
                continue
        return None

    def run(self, selected):
        stage = "checking"
        try:
            if os.name != "nt":
                raise ValueError("当前自动连接仅支持 Windows 微信 4.x。")
            protect_private(self.project)
            self.check_cancelled()
            source = Path(selected["source"])
            area = self.private / "connections" / selected["id"] / self.job["id"]
            area.mkdir(parents=True, exist_ok=False)
            key = self.reusable_key(source, selected["id"])
            reused = key is not None
            if not key:
                stage = "key"
                key = area / "key.txt"
                self.update(stage="key", message="正在准备密钥获取；如需重登微信，将在这里提示。")
                self.run_child("capture_desktop_key.py", ["--db", source / "message/message_0.db", "--output", key, "--timeout", 300])
            self.check_cancelled()
            stage = "decrypting"
            total = sum(bool(re.fullmatch(r"(?:media|message)_\d+\.db", file.name)) for file in (source / "message").glob("*.db"))
            total += int((source / "contact/contact.db").is_file())
            self.update(stage=stage, message="已验证并复用本地密钥，正在生成数据副本…" if reused else "密钥验证成功，正在生成数据副本…",
                        completed=0, total=total, reused_key=reused)
            snapshot, cache = area / "databases", area / "audio"
            self.run_child("prepare_desktop_data.py", ["--source", source, "--key-file", key, "--output", snapshot])
            stage = "indexing"
            self.update(stage=stage, message="正在整理会话、发送人和语音时间…")
            library = VoiceLibrary(snapshot, cache)
            self.check_cancelled()
            config = {"source": str(source), "account": selected["name"], "account_id": selected["id"],
                      "snapshot": str(snapshot.relative_to(self.private)), "cache": str(cache.relative_to(self.private)),
                      "key": str(key.relative_to(self.private)), "updated_at": datetime.now(SHANGHAI).isoformat()}
            temporary = self.private / ("connection-" + self.job["id"] + ".tmp")
            with self.lock:
                self.check_cancelled()
                temporary.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
                temporary.replace(self.private / "connection.json")
                self.activate(library)
                self.settings = config
                self.job.update(state="done", stage="done", message=f"已载入 {len(library.records)} 条语音、{len(library.chats)} 个会话。",
                                voice_count=len(library.records), chat_count=len(library.chats))
        except ConnectionCancelled:
            self.update(state="cancelled", stage="cancelled", message="已取消。当前语音列表和原始微信数据均保留。")
        except Exception as error:
            messages = {"checking": "无法检查目录或私有存储权限，请确认路径可读、项目目录可写后重试。",
                        "key": "未取得有效密钥。请确认使用受支持的 Windows 微信 4.x，并在点击重试后按提示从托盘退出、重新打开并登录。等待上限为 5 分钟。",
                        "decrypting": "数据副本未通过认证或读取失败。请等待微信同步完成后重试；微信版本或密钥变化时可能需要重新获取。",
                        "indexing": "无法识别这份数据的语音结构，当前微信版本可能尚不支持。"}
            self.update(state="error", stage=stage, message=(str(error) if isinstance(error, ValueError) and stage == "checking" else messages[stage]) + " 原来的语音列表未更换。")

    def close(self):
        self.cancel()
        self.executor.shutdown(wait=True)
