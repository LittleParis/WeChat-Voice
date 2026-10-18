"""Native Qt Widgets interface; all archive operations stay inside this window."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty, SimpleQueue
import os
import shutil
import tempfile

from PySide6.QtCore import Qt, QTimer, QUrl, QDate
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QComboBox, QListWidget, QListWidgetItem, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QCheckBox, QDateEdit,
    QProgressBar, QSlider, QFileDialog, QGroupBox, QSizePolicy)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

from desktop_connection import discover_sources, inspect_source
from runtime_paths import system_dll_search

STYLE = """
QMainWindow, QWidget { background: #ffffff; color: #203337; font-size: 13px; }
QWidget#sidebar { background: #f3f7f7; border-right: 1px solid #dce4e5; }
QLabel#title { font-size: 23px; font-weight: 600; }
QLabel#heading { font-size: 19px; font-weight: 600; }
QLabel#muted { color: #526468; }
QPushButton { background: #ffffff; border: 1px solid #c8d5d7; border-radius: 5px; padding: 8px 13px; min-height: 20px; }
QPushButton:hover { background: #edf5f5; }
QPushButton:focus { border: 2px solid #187b89; }
QPushButton[primary="true"] { background: #086c79; color: white; border-color: #086c79; }
QPushButton[primary="true"]:hover { background: #005762; }
QPushButton:disabled { background: #f3f5f5; color: #7d898c; border-color: #e0e6e7; }
QLineEdit, QComboBox, QDateEdit { padding: 7px; min-height: 21px; border: 1px solid #c8d5d7; border-radius: 4px; background: white; }
QLineEdit:focus, QComboBox:focus, QDateEdit:focus { border: 2px solid #187b89; }
QListWidget { border: none; background: #f3f7f7; outline: 0; }
QListWidget::item { padding: 13px 9px; border-bottom: 1px solid #e5eded; }
QListWidget::item:selected { background: #dceff0; color: #075c68; }
QListWidget::item:hover { background: #eaf2f3; }
QTableWidget { border: none; gridline-color: #e6eded; selection-background-color: #e1f1f2; selection-color: #164a50; }
QHeaderView::section { background: #f5f8f8; color: #526468; padding: 10px; border: none; border-bottom: 1px solid #dce4e5; }
QGroupBox { border: 1px solid #dce4e5; border-radius: 5px; margin-top: 10px; padding: 15px 12px 9px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; color: #526468; }
QProgressBar { border: none; background: #e6eded; border-radius: 3px; max-height: 6px; }
QProgressBar::chunk { background: #167a87; border-radius: 3px; }
QSlider::groove:horizontal { background: #dce7e8; height: 5px; border-radius: 2px; }
QSlider::handle:horizontal { background: #086c79; width: 14px; margin: -5px 0; border-radius: 7px; }
"""


def row(*widgets):
    layout = QHBoxLayout()
    layout.setSpacing(10)
    for widget in widgets:
        if isinstance(widget, QWidget):
            if isinstance(widget, QLabel):
                widget.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
            elif isinstance(widget, QComboBox):
                widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            layout.addWidget(widget)
        else:
            layout.addStretch(widget)
    return layout


def label(text="", name=None):
    result = QLabel(text)
    result.setWordWrap(True)
    if name:
        result.setObjectName(name)
    return result


def button(text, callback, primary=False):
    result = QPushButton(text)
    result.clicked.connect(callback)
    result.setProperty("primary", primary)
    result.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return result


def save_audio(library, voice_id, destination):
    source = library.prepare(voice_id)
    destination = Path(destination).resolve()
    if destination == source.resolve():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".voicekeep-", suffix=".tmp", dir=destination.parent)
    os.close(descriptor)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination


_font_loaded = False


def load_system_font():
    global _font_loaded
    if _font_loaded:
        return
    preferred = None
    fonts = Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts"
    for name in ["msyh.ttc", "simsun.ttc", "segoeui.ttf"]:
        if (fonts / name).is_file():
            registered = QFontDatabase.addApplicationFont(str(fonts / name))
            families = QFontDatabase.applicationFontFamilies(registered)
            if not preferred and families:
                preferred = families[0]
    QApplication.instance().setFont(QFont(preferred or "Segoe UI", 10))
    _font_loaded = True


class NativeWindow(QMainWindow):
    def __init__(self, controller, auto_detect=True):
        super().__init__()
        load_system_font()
        self.controller, self.auto_detect = controller, auto_detect
        self.library = None
        self.chats, self.rows, self.locations = [], [], []
        self.chat_id, self.checked_path = "", ""
        self.offset, self.limit = 0, 100
        self.connection_starting = False
        self.connection_job = None
        self.export_job = None
        self.export_seen = None
        self.export_starting = False
        self.source_job_stamp = None
        self.scanning = False
        self.ready = False
        self.closing = False
        self.playing_id = None
        self.play_queue, self.play_index = [], -1
        self.sequence = {}
        self.completed = SimpleQueue()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="native-ui")
        self.futures = set()
        self.setWindowTitle("语音档案")
        self.resize(1180, 820)
        self.setMinimumSize(900, 640)
        self.setStyleSheet(STYLE)
        self._build()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(150)

    def _build(self):
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(22, 18, 22, 12)
        layout.setSpacing(12)
        self.setCentralWidget(central)
        title = label("语音档案", "title")
        self.source_summary = label("正在打开本机档案…", "muted")
        self.refresh_button = button("刷新聊天数据", self.refresh_source)
        self.settings_button = button("连接本机微信", self.toggle_settings)
        layout.addLayout(row(title, self.source_summary, 1, self.refresh_button, self.settings_button))
        self.settings = QGroupBox("连接本机微信")
        setup = QVBoxLayout(self.settings)
        self.source_path = QLineEdit()
        self.source_path.setPlaceholderText("选择微信聊天数据目录，例如 D:\\xwechat_files")
        self.source_path.setAccessibleName("聊天数据目录")
        self.source_path.textChanged.connect(self.invalidate_source)
        self.browse_button = button("选择文件夹…", self.choose_source)
        self.inspect_button = button("检查目录", self.inspect)
        self.detect_button = button("自动查找", self.detect)
        setup.addLayout(row(self.source_path, self.browse_button, self.inspect_button, self.detect_button))
        self.location = QComboBox()
        self.location.setAccessibleName("数据目录")
        self.location.currentIndexChanged.connect(self.choose_location)
        self.location.hide()
        setup.addWidget(self.location)
        self.accounts = QComboBox()
        self.accounts.setAccessibleName("微信账号")
        self.accounts.addItem("先检查目录，再选择账号", "")
        self.accounts.currentIndexChanged.connect(self.update_controls)
        self.connect_button = button("连接并读取语音", self.start_connection, True)
        self.cancel_connection = button("取消连接", self.cancel_connect)
        self.cancel_connection.hide()
        setup.addLayout(row(label("微信账号"), self.accounts, self.connect_button, self.cancel_connection))
        self.source_message = label("支持 xwechat_files、账号目录或 db_storage；不要选择安装目录或 Backup。", "muted")
        setup.addWidget(self.source_message)
        self.connection_progress = QProgressBar()
        self.connection_progress.setTextVisible(False)
        self.connection_progress.hide()
        setup.addWidget(self.connection_progress)
        setup.addWidget(label("首次获取密钥可能需按提示从托盘退出并重新登录微信；程序不会自动关闭微信。", "muted"))
        layout.addWidget(self.settings)
        self.settings.hide()
        splitter = QSplitter(Qt.Orientation.Horizontal)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 14, 12, 12)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索群聊或联系人")
        self.search.setAccessibleName("搜索会话")
        self.search.textChanged.connect(self.render_chats)
        self.chat_kind = QComboBox()
        for text, value in [("全部会话", "all"), ("群聊", "group"), ("联系人", "contact")]:
            self.chat_kind.addItem(text, value)
        self.chat_kind.currentIndexChanged.connect(self.render_chats)
        self.chat_list = QListWidget()
        self.chat_list.setAccessibleName("会话列表")
        self.chat_list.currentItemChanged.connect(self.select_chat)
        self.library_summary = label("尚未连接微信", "muted")
        side.addWidget(self.search)
        side.addWidget(self.chat_kind)
        side.addWidget(self.chat_list, 1)
        side.addWidget(self.library_summary)
        splitter.addWidget(sidebar)
        detail = QWidget()
        body = QVBoxLayout(detail)
        body.setContentsMargins(20, 10, 0, 0)
        self.chat_title = label("先连接微信，准备语音档案", "heading")
        self.result_summary = label("选择目录和账号后，即可按会话整理原始语音。", "muted")
        body.addWidget(self.chat_title)
        body.addWidget(self.result_summary)
        self.senders = QComboBox()
        self.senders.setAccessibleName("发送人")
        self.senders.addItem("全部发送人", "")
        self.senders.currentIndexChanged.connect(self.apply_filters)
        self.sort = QComboBox()
        self.sort.addItem("从新到旧", "desc")
        self.sort.addItem("从旧到新", "asc")
        self.sort.currentIndexChanged.connect(self.apply_filters)
        body.addLayout(row(label("发送人"), self.senders, label("时间顺序"), self.sort))
        self.since_enabled, self.until_enabled = QCheckBox("开始日期"), QCheckBox("结束日期")
        self.since, self.until = QDateEdit(QDate.currentDate()), QDateEdit(QDate.currentDate())
        for field, checkbox in [(self.since, self.since_enabled), (self.until, self.until_enabled)]:
            field.setDisplayFormat("yyyy-MM-dd")
            field.setCalendarPopup(True)
            field.setEnabled(False)
            checkbox.toggled.connect(field.setEnabled)
            checkbox.toggled.connect(self.apply_filters)
            field.dateChanged.connect(self.apply_filters)
        self.reset_button = button("清除筛选", self.clear_filters)
        body.addLayout(row(self.since_enabled, self.since, self.until_enabled, self.until, self.reset_button))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["发送时间", "发送人", "时长"])
        self.table.setAccessibleName("语音列表")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setShowGrid(False)
        self.table.itemSelectionChanged.connect(self.update_controls)
        self.table.cellDoubleClicked.connect(lambda *_: self.play_selected())
        body.addWidget(self.table, 1)
        self.previous = button("上一页", lambda: self.move_page(-1))
        self.next = button("下一页", lambda: self.move_page(1))
        self.page_info = label("", "muted")
        body.addLayout(row(self.page_info, 1, self.previous, self.next))
        self.save_button = button("保存所选 MP3…", self.choose_save)
        self.mobile_button = button("生成手机数据包", lambda: self.start_export("mobile"))
        self.export_button = button("导出当前结果", lambda: self.start_export("files"), True)
        body.addLayout(row(self.save_button, 1, self.mobile_button, self.export_button))
        self.export_progress = QProgressBar()
        self.export_progress.setTextVisible(False)
        self.export_progress.hide()
        body.addWidget(self.export_progress)
        self.export_message = label("", "muted")
        self.cancel_export_button = button("取消导出", self.cancel_export)
        self.open_export_button = button("打开结果文件夹", self.open_export)
        self.cancel_export_button.hide()
        self.open_export_button.hide()
        body.addLayout(row(self.export_message, self.cancel_export_button, self.open_export_button))
        splitter.addWidget(detail)
        splitter.setSizes([270, 870])
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        self.now_playing = label("选择一条语音，双击列表或点击试听。", "muted")
        layout.addWidget(self.now_playing)
        self.play_button = button("试听", self.play_selected)
        self.next_audio_button = button("下一条", self.play_next)
        self.continuous = QCheckBox("连续播放")
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setAccessibleName("播放进度")
        self.seek.sliderReleased.connect(lambda: self.player.setPosition(self.seek.value()))
        self.clock = label("00:00 / 00:00", "muted")
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setMaximumWidth(90)
        self.volume.setAccessibleName("音量")
        layout.addLayout(row(self.play_button, self.next_audio_button, self.continuous, self.seek, self.clock, label("音量"), self.volume))
        self.notice = label("仅在这台电脑处理。", "muted")
        layout.addWidget(self.notice)
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(.8)
        self.player.setAudioOutput(self.audio_output)
        self.volume.valueChanged.connect(lambda value: self.audio_output.setVolume(value / 100))
        self.player.positionChanged.connect(self.update_position)
        self.player.durationChanged.connect(lambda value: self.seek.setRange(0, int(value)))
        self.player.playbackStateChanged.connect(self.update_controls)
        self.player.mediaStatusChanged.connect(self.media_status)
        self.player.errorOccurred.connect(lambda *_: self.message("音频播放失败，请重试或保存 MP3 后检查。", True))
        self.update_controls()

    def message(self, text, error=False):
        self.notice.setText(text)
        self.notice.setStyleSheet("color: #9e352c;" if error else "color: #526468;")

    def submit(self, kind, operation):
        sequence = self.sequence.get(kind, 0) + 1
        self.sequence[kind] = sequence
        future = self.pool.submit(operation)
        self.futures.add(future)
        def complete(result):
            try:
                value, error = result.result(), None
            except Exception as problem:
                value = None
                error = str(problem) if isinstance(problem, ValueError) else "操作未完成（" + type(problem).__name__ + "），请重试。"
            self.completed.put((kind, sequence, value, error))
        future.add_done_callback(complete)
        self.update_controls()

    def poll(self):
        self.futures = {future for future in self.futures if not future.done()}
        if self.closing or self.controller.stopping.is_set():
            if not self.closing:
                self.begin_close()
            if self.controller.stopped.is_set() and not self.futures:
                self.timer.stop()
                self.pool.shutdown(wait=False, cancel_futures=True)
                self.close()
            return
        if self.controller.booted.is_set() and not self.ready:
            if self.controller.error:
                self.source_summary.setText("无法打开本机档案，请确认数据目录可写。")
                self.message("启动失败：" + self.controller.error, True)
                return
            self.ready = True
            self.reload_library()
            status = self.controller.server.connection.status()
            if status["configured"]:
                self.source_path.setText(status["source"])
                self.inspect()
            else:
                self.settings.show()
                if self.auto_detect:
                    self.detect()
        while True:
            try:
                kind, sequence, value, error = self.completed.get_nowait()
            except Empty:
                break
            if sequence != self.sequence.get(kind):
                continue
            self.handle_result(kind, value, error)
        if self.ready:
            self.poll_connection()
            self.poll_export()
            if self.controller.activate_requested.is_set():
                self.controller.activate_requested.clear()
                self.showNormal()
                self.raise_()
                self.activateWindow()
        self.update_controls()

    def handle_result(self, kind, value, error):
        if kind in {"inspect", "detect"}:
            self.scanning = False
        if kind == "connect":
            self.connection_starting = False
        if kind == "export":
            self.export_starting = False
        if error:
            if kind in {"inspect", "detect", "connect"}:
                self.source_message.setText(error)
            self.message(error, True)
            return
        if kind == "inspect":
            self.accept_source(value)
        elif kind == "detect":
            self.locations = value
            self.location.blockSignals(True)
            self.location.clear()
            self.location.addItem("请选择数据目录", None)
            for item in value:
                self.location.addItem(item["path"], item)
            self.location.blockSignals(False)
            self.location.setVisible(len(value) > 1)
            if len(value) == 1:
                self.accept_source(value[0])
            else:
                self.source_message.setText("找到多个目录，请选择。" if value else "未找到数据目录，请点击“选择文件夹”或手动填写后检查。")
        elif kind == "audio":
            voice_id, path, detail = value
            self.playing_id = voice_id
            self.player.setSource(QUrl.fromLocalFile(str(path)))
            self.now_playing.setText(detail)
            self.player.play()
        elif kind == "save":
            self.message("MP3 已保存到：" + str(value))
        elif kind == "export":
            self.export_job = value
            self.export_seen = None

    def toggle_settings(self):
        self.settings.setVisible(not self.settings.isVisible())

    def invalidate_source(self):
        self.checked_path = ""
        self.scanning = False
        self.sequence["inspect"] = self.sequence.get("inspect", 0) + 1
        self.sequence["detect"] = self.sequence.get("detect", 0) + 1
        self.accounts.clear()
        self.accounts.addItem("目录已更改，请先检查", "")
        self.update_controls()

    def choose_source(self):
        path = QFileDialog.getExistingDirectory(self, "选择微信聊天数据目录", self.source_path.text())
        if path:
            self.source_path.setText(path)
            self.inspect()

    def choose_location(self):
        value = self.location.currentData()
        if value:
            self.accept_source(value)

    def inspect(self):
        value = self.source_path.text()
        self.scanning = True
        self.checked_path = ""
        self.source_message.setText("正在检查目录…")
        self.submit("inspect", lambda: inspect_source(value))

    def detect(self):
        self.scanning = True
        self.checked_path = ""
        self.source_message.setText("正在查找常用数据目录…")
        saved = self.controller.server.connection.status()["source"] if self.ready else None
        self.submit("detect", lambda: discover_sources(saved))

    def accept_source(self, result):
        self.source_path.blockSignals(True)
        self.source_path.setText(result["path"])
        self.source_path.blockSignals(False)
        self.checked_path = result["path"]
        self.accounts.clear()
        self.accounts.addItem("请选择微信账号", "")
        for account in result["accounts"]:
            self.accounts.addItem(account["name"], account["id"])
        selected = self.controller.server.connection.status()["account_id"]
        index = self.accounts.findData(selected)
        if len(result["accounts"]) == 1:
            self.accounts.setCurrentIndex(1)
        elif index >= 0:
            self.accounts.setCurrentIndex(index)
        self.source_message.setText(f"找到 {len(result['accounts'])} 个账号，选择后点击“连接并读取语音”。")

    def begin_connect(self, path, account):
        self.player.stop()
        self.sequence["audio"] = self.sequence.get("audio", 0) + 1
        self.connection_starting = True
        self.settings.show()
        self.source_message.setText("正在准备连接…")
        server = self.controller.server
        def connect():
            with server.jobs_lock:
                if any(job["state"] in {"queued", "running"} for job in server.jobs.values()):
                    raise ValueError("请先完成或取消当前导出。")
                return server.connection.start(path, account)
        self.submit("connect", connect)

    def start_connection(self):
        if self.checked_path and self.accounts.currentData():
            self.begin_connect(self.checked_path, self.accounts.currentData())

    def refresh_source(self):
        status = self.controller.server.connection.status()
        if status["configured"]:
            self.begin_connect(status["source"], status["account_id"])

    def cancel_connect(self):
        self.controller.server.connection.cancel()

    def poll_connection(self):
        status = self.controller.server.connection.status()
        self.source_summary.setText("当前账号：" + status["account"] if status["configured"] else "尚未连接微信")
        job = status["job"]
        if job:
            stamp = (job["id"], job["state"], job["stage"], job["completed"], job["message"])
            if stamp != self.source_job_stamp:
                self.source_job_stamp = stamp
                self.source_message.setText(job["message"] + (f" {job['completed']} / {job['total']}" if job["stage"] == "decrypting" else ""))
            self.connection_progress.show()
            self.connection_progress.setRange(0, job["total"] or 0)
            self.connection_progress.setValue(job["completed"])
            if job["state"] != "running":
                self.connection_progress.setRange(0, 1)
                self.connection_progress.setValue(int(job["state"] == "done"))
            if job["state"] == "done" and self.connection_job != job["id"]:
                self.connection_job = job["id"]
                self.reload_library()
                self.message(job["message"])

    def reload_library(self):
        self.player.stop()
        self.playing_id = None
        self.play_queue, self.play_index = [], -1
        self.sequence["audio"] = self.sequence.get("audio", 0) + 1
        self.library = self.controller.server.library
        overview = self.library.overview()
        self.chats = overview["chats"]
        self.library_summary.setText(f"{overview['voice_count']:,} 条语音 · {overview['chat_count']} 个会话\n仅在这台电脑处理")
        self.settings_button.setText("数据源设置" if self.controller.server.connection.status()["configured"] else "连接本机微信")
        self.chat_id = ""
        self.rows = []
        self.render_chats()
        if self.chat_list.count():
            self.chat_list.setCurrentRow(0)
        else:
            self.chat_title.setText("先连接微信，准备语音档案")
            self.show_page()

    def render_chats(self):
        if not self.library:
            return
        remembered = self.chat_id
        self.chat_list.blockSignals(True)
        self.chat_list.clear()
        for chat in self.chats:
            if self.chat_kind.currentData() not in ("all", chat["kind"]):
                continue
            if self.search.text().strip().casefold() not in chat["name"].casefold():
                continue
            item = QListWidgetItem(f"{chat['name']}\n{'群聊' if chat['kind'] == 'group' else '联系人'} · {chat['count']} 条语音")
            item.setData(Qt.ItemDataRole.UserRole, chat["id"])
            item.setToolTip(chat["name"])
            self.chat_list.addItem(item)
            if chat["id"] == remembered:
                self.chat_list.setCurrentItem(item)
        self.chat_list.blockSignals(False)

    def select_chat(self, item, previous=None):
        if not item:
            return
        self.chat_id = item.data(Qt.ItemDataRole.UserRole)
        chat = self.library.chat_index[self.chat_id]
        self.chat_title.setText(chat["name"])
        self.senders.blockSignals(True)
        self.senders.clear()
        self.senders.addItem("全部发送人", "")
        senders = self.library.senders(self.chat_id)
        names = [sender["name"] for sender in senders]
        for sender in senders:
            suffix = " · " + sender["id"][-5:] if names.count(sender["name"]) > 1 else ""
            self.senders.addItem(f"{sender['name']}{suffix}（{sender['count']}）", sender["id"])
        self.senders.blockSignals(False)
        self.apply_filters()

    def filters(self):
        return {"chat": self.chat_id, "sender": self.senders.currentData() or "", "sort": self.sort.currentData(),
                "since": self.since.date().toString("yyyy-MM-dd") if self.since_enabled.isChecked() else "",
                "until": self.until.date().toString("yyyy-MM-dd") if self.until_enabled.isChecked() else ""}

    def clear_filters(self):
        self.senders.setCurrentIndex(0)
        self.since_enabled.setChecked(False)
        self.until_enabled.setChecked(False)
        self.apply_filters()

    def apply_filters(self):
        if not self.library or not self.chat_id:
            return
        try:
            self.rows = self.library.filtered(self.filters())
            self.result_summary.setText(f"{len(self.rows):,} 条语音 · 日期包含起止当天 · 双击一条语音试听")
        except ValueError as error:
            self.rows = []
            self.result_summary.setText(str(error))
        self.offset = 0
        self.show_page()

    def show_page(self):
        visible = self.rows[self.offset:self.offset + self.limit]
        self.table.setRowCount(len(visible))
        for index, voice in enumerate(visible):
            for column, text in enumerate([voice["date"] + "  " + voice["time"], voice["sender"],
                    f"{voice['seconds']:.1f} 秒" if voice["seconds"] is not None else "待确认"]):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, voice["id"])
                self.table.setItem(index, column, item)
            self.table.setRowHeight(index, 49)
        if visible:
            self.table.selectRow(0)
        self.page_info.setText(f"共 {len(self.rows):,} 条 · 第 {self.offset // self.limit + 1} / {max(1, (len(self.rows) + self.limit - 1) // self.limit)} 页")
        self.update_controls()

    def move_page(self, direction):
        self.offset = max(0, min(max(0, len(self.rows) - 1) // self.limit * self.limit, self.offset + direction * self.limit))
        self.show_page()

    def selected_voice(self):
        index = self.table.currentRow()
        if index < 0 or self.offset + index >= len(self.rows):
            return None
        return self.rows[self.offset + index]

    def play_selected(self):
        voice = self.selected_voice()
        if not voice:
            return
        if self.playing_id == voice["id"] and self.player.source().isValid():
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
            else:
                self.player.play()
            return
        self.play_queue = list(self.rows)
        self.play_index = self.offset + self.table.currentRow()
        self.prepare_play(voice)

    def prepare_play(self, voice):
        self.player.stop()
        self.message("正在准备原始语音…")
        library = self.library
        detail = f"{voice['sender']} · {voice['date']} {voice['time']}"
        self.submit("audio", lambda: (voice["id"], library.prepare(voice["id"]), detail))

    def play_next(self):
        if self.play_index + 1 < len(self.play_queue):
            self.play_index += 1
            self.prepare_play(self.play_queue[self.play_index])

    def media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.continuous.isChecked():
            self.play_next()

    def update_position(self, value):
        if not self.seek.isSliderDown():
            self.seek.setValue(int(value))
        def stamp(milliseconds):
            seconds = max(0, int(milliseconds) // 1000)
            return f"{seconds // 60:02}:{seconds % 60:02}"
        self.clock.setText(stamp(value) + " / " + stamp(self.player.duration()))

    def choose_save(self):
        voice = self.selected_voice()
        if not voice:
            return
        folder = self.controller.data_dir / "exports"
        folder.mkdir(parents=True, exist_ok=True)
        dialog = QFileDialog(self, "保存 MP3", str(folder / self.library.download_name(voice["id"])), "MP3 音频 (*.mp3)")
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setDefaultSuffix("mp3")
        if dialog.exec() == QFileDialog.DialogCode.Accepted:
            target = dialog.selectedFiles()[0]
            library = self.library
            self.submit("save", lambda: save_audio(library, voice["id"], target))

    def start_export(self, mode):
        if not self.rows or self.export_starting or (self.export_job and self.export_job["state"] in {"queued", "running"}):
            return
        filters = self.filters()
        self.export_starting = True
        self.submit("export", lambda: self.controller.server.start_export(filters, mode))
        self.export_message.setText("正在准备导出…")

    def poll_export(self):
        if not self.export_job:
            return
        job = self.controller.server.job_status(self.export_job["id"])
        self.export_job = job
        self.export_progress.show()
        self.export_progress.setRange(0, job["total"] or 1)
        self.export_progress.setValue(job["completed"])
        active = job["state"] in {"queued", "running"}
        self.cancel_export_button.setVisible(active)
        self.open_export_button.setVisible(not active and job["state"] in {"done", "cancelled"} and Path(job["output"]).is_dir())
        if active:
            self.export_message.setText(f"正在{'打包' if job['mode'] == 'mobile' else '导出'}：{job['completed']} / {job['total']} 条")
        elif job["state"] == "done":
            self.export_message.setText(f"{'数据包已生成' if job['mode'] == 'mobile' else '导出完成'} · {job.get('exported', 0)} 条" + (f"，{job['failed']} 条失败，详情见清单" if job["failed"] else ""))
        elif job["state"] == "cancelled":
            self.export_message.setText("已取消，已完成的导出文件保留。" if job["mode"] == "files" else "已取消，未生成数据包。")
        else:
            self.export_message.setText(job.get("error", "导出未完成，请重试。"))

    def cancel_export(self):
        if self.export_job:
            self.controller.server.jobs[self.export_job["id"]]["cancel"].set()

    def open_export(self):
        if self.export_job:
            folder = Path(self.export_job["output"])
            if folder.is_dir():
                with system_dll_search():
                    os.startfile(str(folder))

    def update_controls(self):
        if not hasattr(self, "play_button"):
            return
        connecting = self.connection_starting or (self.ready and self.controller.server.connection.busy())
        exporting = self.export_starting or bool(self.export_job and self.export_job["state"] in {"queued", "running"})
        enabled = self.ready and not self.closing
        for widget in [self.source_path, self.browse_button, self.inspect_button, self.detect_button, self.location]:
            widget.setEnabled(enabled and not connecting and not self.scanning)
        self.accounts.setEnabled(enabled and bool(self.checked_path) and not connecting)
        self.connect_button.setEnabled(enabled and bool(self.checked_path) and bool(self.accounts.currentData()) and not connecting and not exporting and not self.scanning)
        self.connect_button.setText("正在处理…" if connecting else "连接并读取语音")
        self.cancel_connection.setVisible(bool(connecting))
        configured = self.ready and self.controller.server.connection.status()["configured"]
        self.refresh_button.setEnabled(enabled and configured and not connecting and not exporting)
        available = enabled and bool(self.rows) and not connecting
        self.export_button.setEnabled(available and not exporting)
        self.mobile_button.setEnabled(available and not exporting)
        self.export_button.setText(f"导出当前 {len(self.rows)} 条")
        selected = self.selected_voice() is not None
        self.save_button.setEnabled(available and selected)
        self.play_button.setEnabled(available and selected)
        self.next_audio_button.setEnabled(enabled and not connecting and self.play_index + 1 < len(self.play_queue))
        playing = hasattr(self, "player") and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        same_voice = self.selected_voice() and self.selected_voice()["id"] == self.playing_id
        self.play_button.setText("暂停" if playing and same_voice else "继续" if same_voice and self.player.playbackState() == QMediaPlayer.PlaybackState.PausedState else "试听")
        self.previous.setEnabled(enabled and self.offset > 0)
        self.next.setEnabled(enabled and self.offset + self.limit < len(self.rows))

    def begin_close(self):
        self.closing = True
        self.player.stop()
        self.centralWidget().setEnabled(False)
        self.message("正在停止任务并退出，请稍候…")
        self.controller.request_stop()

    def closeEvent(self, event):
        if self.controller.stopped.is_set() and not self.futures:
            self.pool.shutdown(wait=False, cancel_futures=True)
            event.accept()
        else:
            event.ignore()
            self.begin_close()


def run(controller):
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("语音档案")
    app.setOrganizationName("VoiceKeep")
    app.setStyle("Fusion")
    load_system_font()
    window = NativeWindow(controller)
    window.show()
    import threading
    threading.Thread(target=controller.boot, name="native-start", daemon=True).start()
    return app.exec()
