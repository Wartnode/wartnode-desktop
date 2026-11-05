"""Native dashboard primitives; values follow the active Qt palette."""
import math
from PySide6.QtCore import Qt, QRectF, QSize, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import (QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget, QPlainTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QDialog, QHBoxLayout, QCheckBox,
    QComboBox, QApplication, QFileDialog, QGridLayout)
from .dashboard import ConnectionStatus


def scroll_dialog(dialog):
    """Keep long forms reachable on small logical desktops at high scaling."""
    from PySide6.QtWidgets import QScrollArea,QLayout
    content=QWidget()
    content.setLayout(dialog.layout())
    content.layout().setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
    outer=QVBoxLayout(dialog);outer.setContentsMargins(0,0,0,0)
    scroll=QScrollArea();scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidgetResizable(True);scroll.setWidget(content);outer.addWidget(scroll)
    area=QApplication.primaryScreen().availableGeometry()
    dialog.resize(min(dialog.width(),area.width()-24),min(dialog.height(),area.height()-40))
    return scroll


def text(value='', muted=False):
    widget = QLabel(value)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setProperty('muted', muted)
    return widget


class MetricPanel(QFrame):
    def __init__(self, title, value='—', detail='', parent=None):
        super().__init__(parent)
        self.setObjectName('panel')
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 16, 18, 16)
        self.body.setSpacing(7)
        self.caption = text(title, True)
        self.value = text(value)
        self.value.setObjectName('value')
        self.detail = text(detail, True)
        self.body.addWidget(self.caption)
        self.body.addWidget(self.value)
        self.body.addWidget(self.detail)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_value(self, value, detail=None):
        self.value.setText(str(value))
        if detail is not None:
            self.detail.setText(str(detail))


class FlowGrid(QWidget):
    """Reflow complete native panels instead of squeezing text at high DPI."""
    def __init__(self, widgets, columns=3, minimum=215, parent=None):
        super().__init__(parent)
        self.widgets, self.columns, self.minimum = list(widgets), columns, minimum
        self.active_columns = 0
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0,0,0,0)
        self.grid.setSpacing(14)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Minimum)
        self.reflow()

    def minimumSizeHint(self):
        return QSize(self.minimum,self.grid.minimumSize().height())

    def reflow(self):
        columns = max(1,min(self.columns,(max(self.minimum,self.width())+14)//(self.minimum+14)))
        if columns == self.active_columns:return
        for widget in self.widgets:self.grid.removeWidget(widget)
        for i,widget in enumerate(self.widgets):self.grid.addWidget(widget,i//columns,i%columns)
        for i in range(self.columns):self.grid.setColumnStretch(i,1 if i<columns else 0)
        self.active_columns = columns
        self.updateGeometry()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.reflow()


class StatusRing(QWidget):
    """Exactly one status, with progress only when it has a reliable denominator."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.status = ConnectionStatus('Offline', 'Node stopped', True, False, 0)
        self.setMinimumSize(140, 140)
        self.set_status(self.status)

    def sizeHint(self):
        return QSize(160, 160)

    def set_status(self, status):
        if status.progress is not None and not 0 <= status.progress <= 1:
            raise ValueError('Invalid connection progress')
        self.status = status
        self.setAccessibleName('Connection: ' + status.name)
        self.setAccessibleDescription(status.detail)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        size = min(self.width(), self.height()) - 18
        area = QRectF((self.width()-size)/2, (self.height()-size)/2, size, size)
        painter.setPen(QPen(palette.color(QPalette.ColorRole.Mid), 6))
        painter.drawEllipse(area)
        colors = {'Synced': '#6dcba1', 'Lite': '#72a7df'}
        color = QColor(colors[self.status.name]) if self.status.name in colors else palette.color(
            QPalette.ColorRole.Highlight if self.status.name == 'Syncing' else QPalette.ColorRole.PlaceholderText)
        painter.setPen(QPen(color, 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self.status.progress is None:
            painter.drawArc(area, 90*16, -90*16)
        elif self.status.progress:
            painter.drawArc(area, 90*16, -round(self.status.progress*360*16))
        painter.setPen(palette.color(QPalette.ColorRole.WindowText))
        font = QFont(self.font())
        font.setPointSize(15)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.drawText(area, Qt.AlignmentFlag.AlignCenter, self.status.name)


class HashrateChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.points = []
        self.setMinimumHeight(160)
        self.setAccessibleName('Janus hashrate history')

    def set_samples(self, samples):
        self.points = [(s.elapsed_s, s.janus_hps/1_000_000) for s in samples][-720:]
        self.setAccessibleDescription('No samples' if not self.points else
                                      f'Latest Janus hashrate {self.points[-1][1]:.2f} MH/s')
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        palette = self.palette()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(55, 20, max(1, self.width()-70), max(1, self.height()-52))
        painter.setPen(palette.color(QPalette.ColorRole.PlaceholderText))
        painter.drawText(55, 14, 'MH/s · Janus')
        if not self.points:
            painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, 'Hashrate appears when mining starts')
            return
        low = min(p[1] for p in self.points)
        high = max(p[1] for p in self.points)
        padding = max((high-low)*.15, high*.02, .01)
        low, high = max(0, low-padding), high+padding
        start, end = self.points[0][0], self.points[-1][0]
        duration = max(1, end-start)
        for i in range(3):
            y = bounds.top()+i*bounds.height()/2
            value = high-i*(high-low)/2
            painter.setPen(QPen(palette.color(QPalette.ColorRole.Mid), 1))
            painter.drawLine(bounds.left(), y, bounds.right(), y)
            painter.setPen(palette.color(QPalette.ColorRole.PlaceholderText))
            painter.drawText(QRectF(0, y-9, 47, 20), Qt.AlignmentFlag.AlignRight, f'{value:.1f}')
        painter.drawText(QRectF(bounds.left(), bounds.bottom()+7, bounds.width()/2, 20),
                         Qt.AlignmentFlag.AlignLeft, f'{duration/60:.0f} min ago')
        painter.drawText(QRectF(bounds.center().x(), bounds.bottom()+7, bounds.width()/2, 20),
                         Qt.AlignmentFlag.AlignRight, 'Latest')
        path = QPainterPath()
        for i, (x, value) in enumerate(self.points):
            x = bounds.left()+(x-start)/duration*bounds.width()
            y = bounds.bottom()-(value-low)/(high-low)*bounds.height()
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        painter.setPen(QPen(palette.color(QPalette.ColorRole.Highlight), 2))
        painter.drawPath(path)


def label(text="", name=None, muted=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    if name:
        widget.setObjectName(name)
    widget.setProperty("muted", muted)
    return widget


def button(text, callback, primary=False):
    widget = QPushButton(text)
    widget.setProperty("primary", primary)
    widget.clicked.connect(callback)
    return widget


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    widget.verticalHeader().hide()
    widget.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    widget.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    widget.setAlternatingRowColors(True)
    return widget


def populate(widget, rows):
    widget.setRowCount(len(rows))
    for y, row in enumerate(rows):
        for x, value in enumerate(row):
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            widget.setItem(y, x, item)
    widget.resizeRowsToContents()


def format_wart(e8):
    return f"{int(e8)//100000000:,}.{int(e8)%100000000:08d}"


class LogView(QPlainTextEdit):
    """Append bounded log updates, following the tail until the reader scrolls up."""
    followingChanged = Signal(bool)
    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setMaximumBlockCount(250)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.lines = ()
        self.raw_lines = ()
        self.level = 'All levels'
        self.following = True
        self.reading_position = 0
        scroll = self.verticalScrollBar()
        scroll.actionTriggered.connect(lambda _: self.set_following(scroll.sliderPosition()))
        scroll.sliderMoved.connect(self.set_following)
        scroll.rangeChanged.connect(lambda *_: QTimer.singleShot(0,self.follow_newest))

    def set_following(self, position):
        self.reading_position = position
        following = position >= self.verticalScrollBar().maximum()-1
        if following != self.following:
            self.following = following
            self.followingChanged.emit(following)

    def set_level(self, level):
        self.level = level
        self.lines = ()
        self.update_lines(self.raw_lines)

    def follow_newest(self):
        scroll = self.verticalScrollBar()
        scroll.setValue(scroll.maximum() if self.following else self.reading_position)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0,self.follow_newest)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0,self.follow_newest)

    def wheelEvent(self, event):
        super().wheelEvent(event)
        self.set_following(self.verticalScrollBar().value())

    def keyPressEvent(self, event):
        super().keyPressEvent(event)
        if event.key() in (Qt.Key.Key_Up,Qt.Key.Key_Down,Qt.Key.Key_PageUp,
                           Qt.Key.Key_PageDown,Qt.Key.Key_Home,Qt.Key.Key_End):
            self.set_following(self.verticalScrollBar().value())

    def update_lines(self, lines):
        self.raw_lines = tuple(lines)[-self.maximumBlockCount():]
        incoming = self.raw_lines
        if self.level == 'Warnings & errors':
            incoming = tuple(line for line in incoming if any(word in line.lower() for word in ('warn','error','fatal','failed')))
        elif self.level == 'Info':
            incoming = tuple(line for line in incoming if 'info' in line.lower())
        if incoming == self.lines:
            return
        scroll = self.verticalScrollBar()
        previous_position = self.reading_position
        follow_tail = self.following
        overlap = next((count for count in range(min(len(self.lines),len(incoming)),0,-1)
                        if self.lines[-count:] == incoming[:count]),0)
        if overlap and overlap < len(incoming):
            added = incoming[overlap:]
            removed = max(0,len(self.lines)+len(added)-self.maximumBlockCount())
            self.appendPlainText('\n'.join(added))
            if not follow_tail:
                self.reading_position = max(0,previous_position-removed)
                scroll.setValue(self.reading_position)
        else:
            # A process restart replaces the stream and starts a fresh tail.
            self.setPlainText('\n'.join(incoming))
            follow_tail = True
        self.lines = incoming
        self.following = follow_tail
        if follow_tail:
            scroll.setValue(scroll.maximum())


class LogDialog(QDialog):
    def __init__(self, title, parent):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 520)
        body = QVBoxLayout(self)
        body.addWidget(label(title, 'title'))
        controls = QHBoxLayout()
        self.level = QComboBox()
        self.level.addItems(['All levels','Info','Warnings & errors'])
        self.level.setAccessibleName('Log level')
        controls.addWidget(self.level)
        controls.addStretch()
        self.follow = QCheckBox('Follow output')
        self.follow.setChecked(True)
        controls.addWidget(self.follow)
        body.addLayout(controls)
        self.view = LogView()
        body.addWidget(self.view, 1)
        self.level.currentTextChanged.connect(self.view.set_level)
        self.follow.toggled.connect(self.set_following)
        self.view.followingChanged.connect(self.reflect_following)
        actions = QHBoxLayout()
        actions.addWidget(button('Copy log',lambda:QApplication.clipboard().setText(self.view.toPlainText())))
        actions.addWidget(button('Export log…',self.export))
        actions.addStretch()
        actions.addWidget(button('Close',self.close))
        body.addLayout(actions)

    def set_following(self, enabled):
        if enabled:
            self.view.set_following(self.view.verticalScrollBar().maximum())
            self.view.follow_newest()
        else:
            self.view.following = False

    def reflect_following(self, enabled):
        self.follow.blockSignals(True)
        self.follow.setChecked(enabled)
        self.follow.blockSignals(False)

    def export(self):
        from pathlib import Path
        path, _ = QFileDialog.getSaveFileName(self,'Export log','','Text log (*.log)')
        if path:
            target = Path(path)
            if target.suffix.lower() != '.log':target = target.with_name(target.name+'.log')
            try:
                target.write_text(self.view.toPlainText(),encoding='utf-8')
            except OSError:
                self.parent().notice.setText('Could not save the log. Choose another location.')
