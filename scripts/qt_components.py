"""Small native view components with bounded rendering and optional motion."""
from PySide6.QtCore import (Qt, QModelIndex, QAbstractTableModel, QVariantAnimation,
                            QEasingCurve, QSize, QObject, Signal)
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QApplication, QAbstractItemView, QTableView, QListWidget,
                              QStyledItemDelegate, QStyleOptionViewItem, QStyle, QLabel)


class WorkerEvents(QObject):
    ready = Signal()


class VoiceModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows, self.display, self.positions = [], [], {}
        self.playing_id = None
        self.playing = False
        self.bold = QFont(QApplication.font())
        self.bold.setWeight(QFont.Weight.DemiBold)
        self.play_icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 3

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return ["发送时间", "发送人", "时长"][section]
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return int(Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if section == 2 else Qt.AlignmentFlag.AlignLeft))
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        voice = self.rows[index.row()]
        active = voice["id"] == self.playing_id
        if role == Qt.ItemDataRole.DisplayRole:
            return self.display[index.row()][index.column()]
        if role == Qt.ItemDataRole.UserRole:
            return voice["id"]
        if role == Qt.ItemDataRole.ToolTipRole:
            return " · ".join(self.display[index.row()]) + (" · 正在播放" if active and self.playing else "")
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if index.column() == 2 else Qt.AlignmentFlag.AlignLeft))
        if active:
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor("#086c79")
            if role == Qt.ItemDataRole.FontRole:
                return self.bold
            if role == Qt.ItemDataRole.DecorationRole and index.column() == 0 and self.playing:
                return self.play_icon
        return None

    def replace(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.positions = {voice["id"]: index for index, voice in enumerate(rows)}
        self.display = [(v["date"] + "  " + v["time"], v["sender"],
                         f"{v['seconds']:.1f} 秒" if v["seconds"] is not None else "待确认") for v in rows]
        self.endResetModel()

    def set_playing(self, voice_id, playing):
        if (voice_id, playing) == (self.playing_id, self.playing):
            return
        changed = {self.positions.get(self.playing_id), self.positions.get(voice_id)} - {None}
        self.playing_id, self.playing = voice_id, playing
        for row in changed:
            self.dataChanged.emit(self.index(row, 0), self.index(row, 2),
                [Qt.ItemDataRole.ForegroundRole, Qt.ItemDataRole.FontRole, Qt.ItemDataRole.DecorationRole])


class SmoothScroll:
    """Animate coarse mouse wheels, retaining native trackpad and scrollbar input."""
    def setup_scroll(self):
        self.motion_enabled = True
        self.scroll_target = 0
        self.scroll_animation = QVariantAnimation(self)
        self.scroll_animation.setDuration(160)
        self.scroll_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.scroll_animation.valueChanged.connect(lambda value: self.verticalScrollBar().setValue(round(value)))
        self.verticalScrollBar().sliderPressed.connect(self.scroll_animation.stop)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

    def set_motion(self, enabled):
        self.motion_enabled = enabled
        if not enabled:
            self.scroll_animation.stop()

    def wheelEvent(self, event):
        bar = self.verticalScrollBar()
        if not self.motion_enabled or not event.pixelDelta().isNull() or not event.angleDelta().y() or event.modifiers():
            self.scroll_animation.stop()
            return super().wheelEvent(event)
        if bar.maximum() <= bar.minimum():
            return super().wheelEvent(event)
        active = self.scroll_animation.state() == QVariantAnimation.State.Running
        start = self.scroll_target if active else bar.value()
        distance = event.angleDelta().y() / 120 * QApplication.wheelScrollLines() * 38
        self.scroll_target = max(bar.minimum(), min(bar.maximum(), round(start - distance)))
        self.scroll_animation.stop()
        self.scroll_animation.setStartValue(float(bar.value()))
        self.scroll_animation.setEndValue(float(self.scroll_target))
        self.scroll_animation.start()
        event.accept()

    def mousePressEvent(self, event):
        self.scroll_animation.stop()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        self.scroll_animation.stop()
        super().keyPressEvent(event)


class VoiceTable(SmoothScroll, QTableView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_scroll()
        self.setModel(VoiceModel(self))
        self.empty = QLabel(self.viewport())
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.empty.setStyleSheet("color: #526468; background: transparent; padding: 24px;")
        self.empty.hide()

    def rowCount(self):
        return self.model().rowCount()

    def currentRow(self):
        return self.currentIndex().row()

    def replace(self, rows, selected=None):
        self.scroll_animation.stop()
        self.setUpdatesEnabled(False)
        try:
            self.model().replace(rows)
            if rows:
                self.selectRow(self.model().positions.get(selected, 0))
            self.scrollToTop()
        finally:
            self.setUpdatesEnabled(True)

    def show_empty(self, text):
        self.empty.setText(text)
        self.empty.setGeometry(self.viewport().rect())
        self.empty.setVisible(self.rowCount() == 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.empty.setGeometry(self.viewport().rect())


class ChatList(SmoothScroll, QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_scroll()
        self.setUniformItemSizes(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setItemDelegate(ChatDelegate(self))


class ChatDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(210, 68)

    def paint(self, painter, option, index):
        item = QStyleOptionViewItem(option)
        self.initStyleOption(item, index)
        title, _, detail = str(index.data(Qt.ItemDataRole.DisplayRole)).partition("\n")
        item.text = ""
        style = item.widget.style() if item.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, item, painter, item.widget)
        painter.save()
        painter.setClipRect(option.rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        area = option.rect.adjusted(13, 10, -13, -9)
        font = QFont(option.font)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor("#075c68" if selected else "#203337"))
        painter.drawText(area.left(), area.top() + 17, painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, area.width()))
        font.setWeight(QFont.Weight.Normal)
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(QColor("#35636a" if selected else "#526468"))
        painter.drawText(area.left(), area.top() + 37, painter.fontMetrics().elidedText(detail, Qt.TextElideMode.ElideRight, area.width()))
        painter.restore()
