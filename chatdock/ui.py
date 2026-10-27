import html
import json
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QSpinBox, QStackedWidget, QTableWidget, QTableWidgetItem,
    QTabWidget, QTextBrowser, QVBoxLayout, QWidget, QAbstractItemView,
)

from . import PARENT_URL, __version__
from .context import Budget, estimate, message_cost, system_prompt
from .engine import Engine
from .network import Cancelled, Client, Task, connect_local, model_context
from .secrets import Vault
from .store import Store, data_dir

STYLE = """
QWidget {font-family:'Microsoft YaHei UI','Segoe UI';font-size:13px;color:#243a40;}
QMainWindow,QStackedWidget {background:#f4f7f7;}
QFrame#sidebar {background:#17343a;}
QLabel#brand {color:#f0fff9;font-size:23px;font-weight:700;}
QLabel#sideMuted {color:#9fbdbe;font-size:11px;}
QLabel#title {font-size:27px;font-weight:700;color:#1b3b40;}
QLabel#eyebrow {color:#4a897b;font-size:11px;font-weight:600;}
QLabel#muted {color:#73858c;}
QLabel#section {font-size:16px;font-weight:600;}
QLabel#badge {background:#dcefe6;color:#227a5d;border-radius:6px;padding:5px 9px;}
QLabel#counter {color:#537c72;padding:5px;}
QFrame#card {background:white;border:1px solid #dce6e6;border-radius:10px;}
QPushButton {background:white;border:1px solid #d0dedc;border-radius:7px;padding:9px 13px;}
QPushButton:hover {background:#e7f1eb;border-color:#9bc2b1;}
QPushButton:disabled {background:#e6ecea;color:#9aa9a7;border-color:#dfe7e4;}
QPushButton#primary {background:#1b7f64;color:white;border-color:#1b7f64;font-weight:600;}
QPushButton#primary:hover {background:#13674f;}
QPushButton#primary:disabled {background:#9bbbae;border-color:#9bbbae;}
QPushButton#nav {background:transparent;color:#b6cdce;border:0;text-align:left;padding:11px 14px;}
QPushButton#nav:checked {background:#2a5053;color:#f2fff9;font-weight:600;}
QPushButton#nav:hover {background:#23464c;}
QListWidget#sessions {background:transparent;border:0;color:#b6cdce;outline:0;}
QListWidget#sessions::item {padding:10px 6px;border-radius:5px;}
QListWidget#sessions::item:selected {background:#2a5053;color:white;}
QLineEdit,QPlainTextEdit,QTextBrowser,QComboBox,QSpinBox {background:white;border:1px solid #d8e3e2;border-radius:7px;padding:8px;selection-background-color:#c4e8d8;}
QLineEdit,QComboBox,QSpinBox {min-height:22px;}
QTextBrowser {background:#f4f7f7;border:0;}
QTableWidget {background:white;border:1px solid #dbe5e3;border-radius:7px;gridline-color:#e7edeb;selection-background-color:#dcf0e6;selection-color:#173c32;}
QHeaderView::section {background:#eef4f1;color:#54736b;padding:9px;border:0;}
QScrollArea {border:0;background:transparent;}
QScrollArea > QWidget > QWidget {background:transparent;}
QTabWidget::pane {border:0;}
QTabBar::tab {background:#e5eeea;padding:10px 17px;margin:0 5px 8px 0;border-radius:5px;}
QTabBar::tab:selected {background:#cce9d9;color:#196e52;}
QToolTip {background:#21443e;color:white;padding:6px;}
"""


def label(text, name=None, wrap=False):
    item = QLabel(text)
    if name:
        item.setObjectName(name)
    item.setWordWrap(wrap)
    return item


def button(text, callback, primary=False):
    item = QPushButton(text)
    if primary:
        item.setObjectName("primary")
    item.setCursor(Qt.CursorShape.PointingHandCursor)
    item.clicked.connect(callback)
    return item


def card():
    frame = QFrame()
    frame.setObjectName("card")
    box = QVBoxLayout(frame)
    box.setContentsMargins(20, 18, 20, 18)
    box.setSpacing(12)
    return frame, box


def icon():
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#1b7f64"))
    p.drawRoundedRect(0, 0, 64, 64, 14, 14)
    p.setPen(QColor("#ffffff"))
    p.setFont(QFont("Segoe UI", 30, QFont.Weight.Bold))
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "C")
    p.end()
    return QIcon(pix)


class Job(QObject):
    event = Signal(str, object)
    success = Signal(object)
    failure = Signal(str)
    finished = Signal()

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.task = Task()

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        try:
            self.success.emit(self.fn(self))
        except Cancelled as exc:
            self.failure.emit(str(exc))
        except Exception as exc:
            self.failure.emit(str(exc))
        finally:
            self.finished.emit()


class Window(QMainWindow):
    def __init__(self, store=None, auto_connect=True):
        super().__init__()
        self.store = store or Store()
        self.engine = Engine(self.store)
        self.vault = Vault(self.store.path.parent / "learning-key.dpapi")
        self.job = None
        self.after_job = None
        self.streaming = ""
        self.pending_text = ""
        self.connected = False
        self.connection_generation = 0
        self.persona_edit_id = 1
        self.session_id = self.store.get("last_session")
        if not any(s["id"] == self.session_id for s in self.store.sessions()):
            self.session_id = self.store.new_session()
        self.setWindowTitle("ModelDock Chat · 独立聊天 SUB")
        self.setWindowIcon(icon())
        self.setMinimumSize(980, 650)
        area = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1240, area.width() - 40), min(880, area.height() - 40))
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 25, 18, 18)
        side.setSpacing(8)
        side.addWidget(label("◈  ModelDock", "brand"))
        side.addWidget(label("CHAT / 独立聊天 SUB", "sideMuted"))
        side.addSpacing(18)
        self.nav = []
        for i, title in enumerate(["◌   聊天主页", "✧   人格工作室", "▤   记忆与用户", "◇   黑话学习", "⌘   连接设置"]):
            nav = button(title, lambda checked=False, index=i: self.navigate(index))
            nav.setObjectName("nav")
            nav.setCheckable(True)
            side.addWidget(nav)
            self.nav.append(nav)
        side.addSpacing(14)
        self.new_button = button("＋  新的对话", self.new_session)
        side.addWidget(self.new_button)
        self.session_list = QListWidget()
        self.session_list.setObjectName("sessions")
        self.session_list.currentItemChanged.connect(self.select_session)
        side.addWidget(self.session_list, 1)
        self.connection_badge = label("○  等待连接本地模型", "sideMuted", True)
        side.addWidget(self.connection_badge)
        side.addWidget(button("打开主项目 ↗", lambda: QDesktopServices.openUrl(QUrl(PARENT_URL))))
        side.addWidget(label(f"v{__version__} · OpenAI Codex 协作", "sideMuted"))
        layout.addWidget(sidebar)
        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        content.addWidget(self.stack, 1)
        footer = QHBoxLayout()
        footer.setContentsMargins(24, 5, 24, 12)
        self.status = label("所有主聊天均连接本机 Ollama。", "muted", True)
        footer.addWidget(self.status, 1)
        self.stop_button = button("停止当前任务", self.stop)
        self.stop_button.setEnabled(False)
        footer.addWidget(self.stop_button)
        content.addLayout(footer)
        layout.addLayout(content, 1)
        self.setCentralWidget(root)
        self.build_chat()
        self.build_persona()
        self.build_memory()
        self.build_learning()
        self.build_connection()
        self.refresh_personas()
        self.refresh_sessions()
        self.refresh_memory()
        self.refresh_knowledge()
        self.render_chat()
        self.navigate(0)
        self.update_counter()
        if auto_connect:
            QTimer.singleShot(100, self.connect)

    def page(self, eyebrow, title, subtitle, scroll=True):
        body = QWidget()
        box = QVBoxLayout(body)
        box.setContentsMargins(28, 24, 28, 18)
        box.setSpacing(14)
        box.addWidget(label(eyebrow, "eyebrow"))
        box.addWidget(label(title, "title"))
        box.addWidget(label(subtitle, "muted", True))
        if scroll:
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setWidget(body)
            self.stack.addWidget(area)
        else:
            self.stack.addWidget(body)
        return box

    def navigate(self, index):
        self.stack.setCurrentIndex(index)
        for i, nav in enumerate(self.nav):
            nav.setChecked(i == index)

    def error(self, message):
        self.status.setText(message)
        QMessageBox.warning(self, "操作提示", message)

    def run_job(self, fn, success=None, kind="task"):
        if self.job:
            self.status.setText("请等待或停止当前任务后再操作。")
            return
        self.after_job = None
        self.job_kind = kind
        self.job = Job(fn)
        self.job.event.connect(self.job_event)
        if success:
            self.job.success.connect(success)
        self.job.failure.connect(self.job_failed)
        self.job.finished.connect(self.job_finished)
        self.set_busy(True)
        QTimer.singleShot(0, self.job.start)

    def set_busy(self, busy):
        for widget in [self.new_button, self.session_list, self.model_combo, self.context_combo,
                       self.persona_combo, self.connect_button, self.learn_button, self.analyze_button,
                       self.persona_save, self.profile_save, self.variant_button, self.provider_save]:
            widget.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
        self.update_counter()
        self.persona_counter()
        self.learning_counter()

    def job_event(self, kind, value):
        if kind == "token":
            self.streaming += value
            self.render_chat()
        elif kind == "session":
            self.session_id = value
            self.pending_text = ""
            self.refresh_sessions()
            self.render_chat()
        elif kind == "retrieval":
            self.retrieval.setText("知识参考：" + str(value))
        else:
            self.status.setText(str(value))

    def job_failed(self, message):
        self.after_job = None
        self.status.setText(message)
        if self.job_kind == "chat" and not self.prompt.toPlainText():
            self.prompt.setPlainText(self.last_sent)

    def job_finished(self):
        callback = self.after_job
        self.after_job = None
        self.job = None
        self.streaming = ""
        self.pending_text = ""
        self.set_busy(False)
        self.refresh_sessions()
        self.render_chat()
        self.refresh_memory()
        self.refresh_knowledge()
        if callback:
            QTimer.singleShot(0, callback)

    def stop(self):
        if self.job:
            self.job.task.cancel()
            self.stop_button.setEnabled(False)
            self.status.setText("正在停止…")

    def build_chat(self):
        box = self.page("YOUR LOCAL COMPANION", "今天，想聊些什么？", "人格、长期记忆和学会的黑话，让每次对话都更贴近你。", False)
        row = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(180)
        self.model_combo.setPlaceholderText("先一键连接模型")
        self.model_combo.currentTextChanged.connect(self.select_model)
        row.addWidget(self.model_combo, 2)
        self.persona_combo = QComboBox()
        self.persona_combo.currentIndexChanged.connect(self.select_persona)
        row.addWidget(self.persona_combo, 1)
        row.addWidget(button("删除会话", self.delete_session))
        box.addLayout(row)
        self.retrieval = label("知识参考：发送时自动检索已确认的词条。", "muted", True)
        box.addWidget(self.retrieval)
        self.transcript = QTextBrowser()
        self.transcript.setOpenLinks(False)
        self.transcript.setMinimumHeight(180)
        box.addWidget(self.transcript, 1)
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText("说点什么…  Shift / Enter 均可换行，点击发送开始对话")
        self.prompt.setMaximumHeight(110)
        self.prompt.textChanged.connect(self.update_counter)
        box.addWidget(self.prompt)
        self.counter = label("", "counter", True)
        box.addWidget(self.counter)
        row = QHBoxLayout()
        self.auto_memory = QCheckBox("回复后用本机 AI 整理记忆候选")
        self.auto_memory.setChecked(bool(self.store.get("auto_memory", False)))
        self.auto_memory.toggled.connect(lambda value: self.store.set("auto_memory", value))
        row.addWidget(self.auto_memory, 1)
        self.send_button = button("发送消息  ↑", self.send, True)
        row.addWidget(self.send_button)
        box.addLayout(row)

    def refresh_sessions(self):
        self.session_list.blockSignals(True)
        self.session_list.clear()
        for session in self.store.sessions():
            item = QListWidgetItem(session["title"])
            item.setData(Qt.ItemDataRole.UserRole, session["id"])
            item.setToolTip(f"会话 #{session['id']}" + (f" · 续自 #{session['parent_id']}" if session["parent_id"] else ""))
            self.session_list.addItem(item)
            if session["id"] == self.session_id:
                self.session_list.setCurrentItem(item)
        self.session_list.blockSignals(False)
        self.store.set("last_session", self.session_id)

    def select_session(self, item, old=None):
        if not item or self.job:
            return
        self.session_id = item.data(Qt.ItemDataRole.UserRole)
        persona_id = self.store.session(self.session_id)["persona_id"]
        self.persona_combo.blockSignals(True)
        self.persona_combo.setCurrentIndex(self.persona_combo.findData(persona_id))
        self.persona_combo.blockSignals(False)
        self.navigate(0)
        self.render_chat()
        self.update_counter()
        self.store.set("last_session", self.session_id)

    def new_session(self, checked=False, persona_id=None):
        if self.job:
            return
        ident = persona_id or self.persona_combo.currentData() or 1
        self.session_id = self.store.new_session(ident)
        self.persona_combo.blockSignals(True)
        self.persona_combo.setCurrentIndex(self.persona_combo.findData(ident))
        self.persona_combo.blockSignals(False)
        self.refresh_sessions()
        self.prompt.clear()
        self.render_chat()
        self.navigate(0)

    def delete_session(self):
        if self.job:
            return
        if QMessageBox.question(self, "删除会话", "删除当前会话及其消息？已确认的长期记忆和黑话词库会保留。") != QMessageBox.StandardButton.Yes:
            return
        self.store.delete_session(self.session_id)
        sessions = self.store.sessions()
        self.session_id = sessions[0]["id"] if sessions else self.store.new_session()
        self.refresh_sessions()
        self.select_session(self.session_list.currentItem())

    def render_chat(self):
        rows = self.store.messages(self.session_id)
        content = []
        summary = self.store.session(self.session_id)["summary"]
        if summary:
            content.append("<p style='color:#68877b'>已从上一会话携带摘要，完整记录仍保存在原会话。</p>")
        for row in rows:
            who = "你" if row["role"] == "user" else "助手"
            flag = "" if row["status"] in {"complete", "pending"} else " · 已中断"
            content.append(self.bubble(who + flag, row["content"], row["role"] == "user"))
        if self.pending_text:
            content.append(self.bubble("你", self.pending_text, True))
        if self.streaming:
            content.append(self.bubble("助手 · 正在生成", self.streaming))
        if not content:
            content.append("<div style='color:#7a9289;padding:45px 20px'><h2>在你的电脑上，慢慢聊。</h2><p>先连接 ModelDock 中已部署的模型，选一个人格，就可以开始了。</p><p>记忆和黑话在确认后，会自动成为下一轮对话的参考。</p></div>")
        self.transcript.setHtml("<body style='font-family:Microsoft YaHei UI;font-size:13px'>" + "".join(content) + "</body>")
        bar = self.transcript.verticalScrollBar()
        bar.setValue(bar.maximum())

    @staticmethod
    def bubble(who, text, user=False):
        bg = "#e0f0e7" if user else "#ffffff"
        value = html.escape(text).replace("\n", "<br>")
        return f"<table width='100%' cellpadding='14' cellspacing='0' style='margin:8px 0;background:{bg};'><tr><td><b style='color:#397c67'>{html.escape(who)}</b><br><br>{value}</td></tr></table><br>"

    def update_counter(self):
        if not hasattr(self, "counter"):
            return
        try:
            stats = self.engine.preview(self.session_id, self.prompt.toPlainText())
            warn = ""
            if stats["too_long"]:
                warn = " ⚠ 单条输入超限，请缩短后发送。"
            elif stats["rollover"]:
                warn = " ⚠ 历史将自动整理为摘要，并新开续聊会话。"
            elif stats["used"] > stats["limit"] * .85:
                warn = " 接近上下文上限。"
            self.counter.setText(f"上下文 {self.engine.budget.context} · 输入保守估算 {stats['used']} / {stats['limit']} · 已预留回复 {self.engine.budget.reply}" + warn)
            self.counter.setStyleSheet("color:#bb6542;" if warn else "color:#537c72;")
            self.send_button.setEnabled(not self.job and self.connected and not stats["too_long"] and bool(self.prompt.toPlainText().strip()))
        except (ValueError, AttributeError):
            self.send_button.setEnabled(False)

    def send(self):
        if self.job:
            return
        text = self.prompt.toPlainText().strip()
        if not text:
            return
        stats = self.engine.preview(self.session_id, text)
        if stats["too_long"]:
            self.error("单条消息超过当前模型的上下文预算，请缩短消息或调整上下文。原文已保留。")
            return
        if not self.connected:
            self.error("请先一键连接本地模型。")
            return
        self.last_sent = text
        self.pending_text = text
        self.streaming = ""
        self.prompt.clear()
        ident = self.session_id
        def done(session_id):
            self.session_id = session_id
            self.status.setText("回复完成。")
            if self.auto_memory.isChecked():
                self.after_job = self.analyze_memory
        self.run_job(lambda j: self.engine.chat(ident, text, j.task, j.event.emit), done, "chat")
        self.render_chat()

    def build_persona(self):
        box = self.page("PERSONALITY STUDIO", "为你的 AI，设定性格", "人格提示词会作为主聊天的系统提示词；切换人格会开启新的会话。")
        frame, col = card()
        self.persona_editor_combo = QComboBox()
        self.persona_editor_combo.currentIndexChanged.connect(self.load_persona)
        col.addWidget(self.persona_editor_combo)
        self.persona_name = QLineEdit()
        self.persona_name.setPlaceholderText("人格名称")
        col.addWidget(self.persona_name)
        self.persona_prompt = QPlainTextEdit()
        self.persona_prompt.setMinimumHeight(260)
        self.persona_prompt.setPlaceholderText("描述说话方式、边界、称呼习惯和回答偏好…")
        self.persona_prompt.textChanged.connect(self.persona_counter)
        col.addWidget(self.persona_prompt)
        self.persona_limit = label("", "muted", True)
        col.addWidget(self.persona_limit)
        row = QHBoxLayout()
        self.persona_save = button("保存人格", self.save_persona, True)
        row.addWidget(self.persona_save)
        row.addWidget(button("新建人格", self.new_persona))
        row.addWidget(button("用这个人格开始聊天", lambda: self.new_session(persona_id=self.persona_edit_id)))
        row.addStretch()
        col.addLayout(row)
        box.addWidget(frame)
        box.addWidget(label("长度检查依据当前模型的上下文设置，预留回复、系统指令及至少一条用户消息。主聊天发送时会再把记忆和历史计入总预算。", "muted", True))
        box.addStretch()

    def refresh_personas(self):
        current = self.store.session(self.session_id)["persona_id"]
        for combo in [self.persona_combo, self.persona_editor_combo]:
            combo.blockSignals(True)
            combo.clear()
            for item in self.store.personas():
                combo.addItem(item["name"], item["id"])
            selected = self.persona_edit_id if combo is self.persona_editor_combo else current
            combo.setCurrentIndex(max(0, combo.findData(selected)))
            combo.blockSignals(False)
        self.load_persona()

    def select_persona(self):
        if not self.job and self.persona_combo.currentData():
            self.new_session(persona_id=self.persona_combo.currentData())

    def load_persona(self):
        ident = self.persona_editor_combo.currentData()
        if not ident:
            return
        row = self.store.persona(ident)
        self.persona_edit_id = ident
        self.persona_name.setText(row["name"])
        self.persona_prompt.setPlainText(row["prompt"])

    def persona_counter(self):
        if not hasattr(self, "persona_limit"):
            return
        value = self.persona_prompt.toPlainText()
        cost = message_cost([{"role": "system", "content": system_prompt(value)}, {"role": "user", "content": "x" * 256}])
        self.persona_limit.setText(f"人格占用 {estimate(value)}（UTF-8 保守估算），包含系统与用户预留后 {cost} / {self.engine.budget.input_limit}。" + (" ⚠ 超限，不能保存。" if cost > self.engine.budget.input_limit else ""))
        self.persona_save.setEnabled(not self.job and cost <= self.engine.budget.input_limit)

    def new_persona(self):
        if self.job:
            return
        self.persona_edit_id = None
        self.persona_name.clear()
        self.persona_prompt.clear()

    def save_persona(self):
        if self.job:
            return
        try:
            prompt = self.persona_prompt.toPlainText()
            self.engine.budget.require([{"role": "system", "content": system_prompt(prompt)}, {"role": "user", "content": "x" * 256}])
            self.persona_edit_id = self.store.save_persona(self.persona_edit_id, self.persona_name.text(), prompt)
            self.refresh_personas()
            self.update_counter()
            self.status.setText("人格已保存，后续请求使用最新提示词。")
        except ValueError as exc:
            self.error(str(exc))

    def build_memory(self):
        box = self.page("MEMORY & YOU", "记住你希望被记住的事", "用户资料由你填写；AI 只提取有原文证据的记忆候选，经确认后才参与主聊天。")
        frame, col = card()
        col.addWidget(label("你的资料与偏好", "section"))
        self.user_profile = QPlainTextEdit()
        self.user_profile.setPlainText(self.store.get("user_profile", ""))
        self.user_profile.setPlaceholderText("希望如何称呼你、回答风格、长期目标…")
        self.user_profile.setMaximumHeight(110)
        col.addWidget(self.user_profile)
        self.profile_save = button("保存用户资料", self.save_profile, True)
        col.addWidget(self.profile_save, alignment=Qt.AlignmentFlag.AlignLeft)
        col.addWidget(label("资料最多 500 个 UTF-8 字节，约 160 个中文字符；会占用主聊天上下文。", "muted", True))
        box.addWidget(frame)
        row = QHBoxLayout()
        row.addWidget(label("长期记忆", "section"))
        row.addStretch()
        self.analyze_button = button("用本地 AI 分析当前会话", self.analyze_memory)
        row.addWidget(self.analyze_button)
        row.addWidget(button("手动添加", self.add_memory))
        box.addLayout(row)
        self.memory_table = self.table(["记忆内容", "状态", "原文证据", "来源"])
        box.addWidget(self.memory_table)
        row = QHBoxLayout()
        row.addWidget(button("确认选中记忆", lambda: self.approve_items("memories", self.memory_table)))
        row.addWidget(button("编辑选中", self.edit_memory))
        row.addWidget(button("删除选中", lambda: self.delete_items("memories", self.memory_table)))
        row.addStretch()
        box.addLayout(row)
        box.addWidget(label("记忆分析使用本地模型，不使用黑话学习的外部 API。删除会话不会自动删除已保存的独立记忆。", "muted", True))
        box.addStretch()

    @staticmethod
    def table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setMinimumHeight(240)
        return table

    def fill_table(self, table, rows, keys):
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, key in enumerate(keys):
                value = row.get(key, "")
                if key == "status":
                    value = "已启用" if value == "approved" else "待确认"
                item = QTableWidgetItem(str(value or ""))
                item.setData(Qt.ItemDataRole.UserRole, row["id"])
                item.setToolTip(str(value or ""))
                table.setItem(i, j, item)

    @staticmethod
    def selected_ids(table):
        return [table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole) for index in table.selectionModel().selectedRows()]

    def refresh_memory(self):
        self.fill_table(self.memory_table, self.store.items("memories"), ["content", "status", "evidence", "source_session"])

    def save_profile(self):
        text = self.user_profile.toPlainText().strip()
        if estimate(text) > 500:
            self.error("用户资料超过 500 个 UTF-8 字节，请缩短。原文已保留。")
            return
        self.store.set("user_profile", text)
        self.update_counter()
        self.status.setText("用户资料已保存。")

    def add_memory(self):
        text, ok = QInputDialog.getMultiLineText(self, "手动记忆", "填写希望 AI 记住的事实或偏好：")
        if ok and text.strip():
            if estimate(text) > 500:
                self.error("单条记忆请控制在 500 个 UTF-8 字节以内。")
                return
            self.store.add_memory(text, status="approved")
            self.refresh_memory()
            self.update_counter()

    def edit_memory(self):
        ids = self.selected_ids(self.memory_table)
        if len(ids) != 1:
            self.status.setText("请只选择一条记忆。")
            return
        row = self.store.all("SELECT * FROM memories WHERE id=?", (ids[0],))[0]
        text, ok = QInputDialog.getMultiLineText(self, "编辑记忆", "修改后需重新确认：", row["content"])
        if ok and text.strip():
            if estimate(text) > 500:
                self.error("单条记忆请控制在 500 个 UTF-8 字节以内。")
                return
            self.store.execute("UPDATE memories SET content=?,status='pending' WHERE id=?", (text.strip(), ids[0]))
            self.refresh_memory()
            self.update_counter()

    def analyze_memory(self):
        if self.job:
            return
        if not self.connected:
            self.error("请先连接本地模型。")
            return
        ident = self.session_id
        self.run_job(lambda j: self.engine.analyze_memory(ident, j.task, j.event.emit),
            lambda count: self.status.setText(f"已整理 {count} 条记忆候选，请在“记忆与用户”确认。"), "memory")

    def approve_items(self, table_name, table):
        ids = self.selected_ids(table)
        self.store.approve(table_name, ids)
        self.refresh_memory()
        self.refresh_knowledge()
        self.update_counter()
        self.status.setText(f"已确认 {len(ids)} 条，下一轮主聊天可使用。")

    def delete_items(self, table_name, table):
        ids = self.selected_ids(table)
        if not ids:
            return
        if QMessageBox.question(self, "删除记录", f"删除选中的 {len(ids)} 条记录？") != QMessageBox.StandardButton.Yes:
            return
        self.store.delete_items(table_name, ids)
        self.refresh_memory()
        self.refresh_knowledge()
        self.update_counter()

    def build_learning(self):
        box = self.page("SLANG LAB", "听懂你们圈子里的话", "独立学习、审核入库、主聊天检索使用。长材料会拆为多个独立学习对话。")
        tabs = QTabWidget()
        lab = QWidget()
        col = QVBoxLayout(lab)
        self.learning_mode = QComboBox()
        self.learning_mode.addItems(["使用本机模型学习", "使用手动指定的提供商 API"])
        col.addWidget(self.learning_mode)
        self.material = QPlainTextEdit()
        self.material.setPlaceholderText("粘贴黑话、释义、例句或圈内对话。例如：\nYYDS：永远的神，用来表达赞赏。\n这波操作真是 YYDS。")
        self.material.setMinimumHeight(250)
        self.material.textChanged.connect(self.learning_counter)
        col.addWidget(self.material)
        self.learning_limit = label("", "muted", True)
        col.addWidget(self.learning_limit)
        col.addWidget(label("选择外部 API 时，仅发送这里的学习材料，不发送主聊天、用户资料或记忆。候选仍需你确认。", "muted", True))
        self.learn_button = button("开始独立学习", self.learn, True)
        col.addWidget(self.learn_button, alignment=Qt.AlignmentFlag.AlignLeft)
        col.addStretch()
        tabs.addTab(lab, "学习台")
        provider = QWidget()
        form = QFormLayout(provider)
        form.setSpacing(14)
        self.provider_base = QLineEdit(self.store.get("provider_base", ""))
        self.provider_base.setPlaceholderText("https://你的提供商/v1")
        self.provider_model = QLineEdit(self.store.get("provider_model", ""))
        self.provider_model.setPlaceholderText("提供商支持的模型 ID")
        self.provider_key = QLineEdit()
        self.provider_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.provider_key.setPlaceholderText("API Key（本机 Ollama 可留空）")
        self.provider_context = QSpinBox()
        self.provider_context.setRange(2048, 262144)
        self.provider_context.setSingleStep(1024)
        self.provider_context.setValue(int(self.store.get("provider_context", 8192)))
        self.remember_key = QCheckBox("用 Windows 用户加密保存 Key")
        self.remember_key.setChecked(self.vault.path.exists())
        try:
            self.provider_key.setText(self.vault.load())
        except Exception:
            self.status.setText("已保存的 API Key 无法解密，请在黑话学习提供商设置中重新填写。")
        form.addRow("Base URL", self.provider_base)
        form.addRow("模型 ID", self.provider_model)
        form.addRow("API Key", self.provider_key)
        form.addRow("提供商模型上下文", self.provider_context)
        form.addRow("", self.remember_key)
        self.provider_save = button("保存学习 API 配置", self.save_provider, True)
        form.addRow("", self.provider_save)
        form.addRow(label("此 API 仅供黑话学习使用。使用 OpenAI 兼容的 /chat/completions；Base URL 通常以 /v1 结尾。上下文需按提供商模型说明填写，程序无法保证其声明值。", "muted", True))
        tabs.addTab(provider, "学习提供商 API")
        library = QWidget()
        lib = QVBoxLayout(library)
        self.knowledge_table = self.table(["词语", "释义", "状态", "来源"])
        lib.addWidget(self.knowledge_table)
        row = QHBoxLayout()
        row.addWidget(button("确认选中并提供给主 AI", lambda: self.approve_items("knowledge", self.knowledge_table), True))
        row.addWidget(button("手动添加", lambda: self.edit_knowledge(new=True)))
        row.addWidget(button("编辑", self.edit_knowledge))
        row.addWidget(button("删除", lambda: self.delete_items("knowledge", self.knowledge_table)))
        lib.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(button("导入词库", self.import_knowledge))
        row.addWidget(button("导出已确认词库", self.export_knowledge))
        self.variant_button = button("保存为 Ollama 派生模型", self.create_variant)
        row.addWidget(self.variant_button)
        lib.addLayout(row)
        lib.addWidget(label("词库持久保存于本机数据库。派生模型把已确认词条和人格写入新模型的系统提示，其他 Ollama 客户端也可使用；这不会修改原模型权重，不是微调。大量词条建议继续使用检索。", "muted", True))
        self.learning_history = QPlainTextEdit()
        self.learning_history.setReadOnly(True)
        self.learning_history.setMaximumHeight(110)
        self.learning_history.setPlaceholderText("独立学习记录")
        lib.addWidget(self.learning_history)
        tabs.addTab(library, "词库与模型")
        self.learning_tabs = tabs
        box.addWidget(tabs)
        box.addStretch()
        self.learning_mode.currentIndexChanged.connect(self.learning_counter)
        self.provider_context.valueChanged.connect(self.learning_counter)
        self.learning_counter()

    def learning_counter(self):
        if not hasattr(self, "learning_limit") or not hasattr(self, "provider_context"):
            return
        count = estimate(self.material.toPlainText())
        context = self.provider_context.value() if self.learning_mode.currentIndex() else self.engine.budget.context
        size = max(100, min(5000, Budget(context, 1024).input_limit - 800))
        self.learning_limit.setText(f"材料 {count} UTF-8 字节 · 独立上下文 {context} · 预计拆成 {max(1, (count + size - 1) // size)} 段。材料总量上限 200,000 字节。")
        self.learn_button.setEnabled(not self.job and 0 < count <= 200000)

    def provider(self):
        base = self.provider_base.text().strip()
        key = self.provider_key.text().strip()
        model = self.provider_model.text().strip()
        Client(base, key)
        if not model:
            raise ValueError("请填写提供商模型 ID。")
        return {"base": base, "key": key, "model": model, "context": self.provider_context.value()}

    def save_provider(self):
        try:
            config = self.provider()
            for key in ("base", "model", "context"):
                self.store.set("provider_" + key, config[key])
            self.vault.save(config["key"] if self.remember_key.isChecked() else "")
            self.status.setText("学习 API 配置已保存；主聊天继续使用本机模型。")
        except (ValueError, OSError, RuntimeError) as exc:
            self.error(str(exc))

    def learn(self):
        if self.job:
            return
        text = self.material.toPlainText().strip()
        if not text or estimate(text) > 200000:
            self.error("请提供不超过 200,000 字节的学习材料。")
            return
        try:
            provider = self.provider() if self.learning_mode.currentIndex() else None
            if provider is None and not self.connected:
                raise ValueError("请先连接本机模型，或配置独立学习 API。")
        except ValueError as exc:
            self.error(str(exc))
            return
        def done(result):
            self.status.setText(f"学习完成：{result['sessions']} 个独立对话，整理 {result['count']} 条候选。确认后提供给主 AI。")
            self.learning_tabs.setCurrentIndex(2)
        self.run_job(lambda j: self.engine.learn(text, j.task, j.event.emit, provider), done, "learning")

    def refresh_knowledge(self):
        self.fill_table(self.knowledge_table, self.store.items("knowledge"), ["term", "definition", "status", "source"])
        lines = [f"#{r['id']} · {r['model']} · {r['status']} · {estimate(r['input'])} 字节" for r in self.store.items("learning")[:20]]
        self.learning_history.setPlainText("\n".join(lines))

    def edit_knowledge(self, checked=False, new=False):
        ids = self.selected_ids(self.knowledge_table)
        if not new and len(ids) != 1:
            self.status.setText("请只选择一个词条。")
            return
        row = {} if new else self.store.all("SELECT * FROM knowledge WHERE id=?", (ids[0],))[0]
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑词条 · 保存后需确认")
        dialog.resize(520, 420)
        form = QFormLayout(dialog)
        fields = {}
        for key, title in [("term", "词语"), ("definition", "释义"), ("example", "例句"), ("aliases", "别名（逗号分隔）")]:
            editor = QPlainTextEdit() if key in {"definition", "example"} else QLineEdit()
            if isinstance(editor, QPlainTextEdit):
                editor.setPlainText(row.get(key, ""))
            else:
                editor.setText(row.get(key, ""))
            fields[key] = editor
            form.addRow(title, editor)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        controls.accepted.connect(dialog.accept)
        controls.rejected.connect(dialog.reject)
        form.addRow(controls)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = {k: (v.toPlainText() if isinstance(v, QPlainTextEdit) else v.text()).strip() for k, v in fields.items()}
        if not values["term"] or not values["definition"] or estimate(json.dumps(values, ensure_ascii=False)) > 4000:
            self.error("词语与释义不能为空，总长度请控制在 4000 UTF-8 字节以内。")
            return
        if new:
            self.store.add_knowledge(**values)
        else:
            self.store.execute("UPDATE knowledge SET term=?,definition=?,example=?,aliases=?,status='pending' WHERE id=?",
                (values["term"], values["definition"], values["example"], values["aliases"], ids[0]))
        self.refresh_knowledge()
        self.update_counter()

    def export_knowledge(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出词库", "ModelDock-Chat-knowledge.json", "JSON (*.json)")
        if path:
            try:
                Path(path).write_text(json.dumps(self.store.export_knowledge(), ensure_ascii=False, indent=2), "utf-8")
                self.status.setText("已导出已确认词条，不包含聊天与个人记忆。")
            except OSError as exc:
                self.error(str(exc))

    def import_knowledge(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入词库", "", "JSON (*.json)")
        if path:
            try:
                if Path(path).stat().st_size > 10_000_000:
                    raise ValueError("文件超过 10 MB。")
                count = self.store.import_knowledge(json.loads(Path(path).read_text("utf-8-sig")))
                self.refresh_knowledge()
                self.status.setText(f"导入 {count} 条候选，确认后生效。")
            except (OSError, ValueError) as exc:
                self.error(str(exc))

    def create_variant(self):
        if self.job:
            return
        if not self.connected:
            self.error("请先连接本机模型。")
            return
        name, ok = QInputDialog.getText(self, "新建派生模型", "新名称（不会覆盖原模型；仅固化人格与已确认词库）：", text="modeldock-chat-slang")
        if ok and name.strip():
            ident = self.store.session(self.session_id)["persona_id"]
            self.run_job(lambda j: self.engine.create_variant(ident, name.strip(), j.task),
                lambda result: self.status.setText(f"派生模型 {result} 已创建，可在其他 Ollama 客户端中选择。"), "variant")

    def build_connection(self):
        box = self.page("COMPANION CONNECTION", "与 ModelDock，一键接上", "两个项目独立运行，通过标准本地 Ollama API 兼容。")
        frame, col = card()
        col.addWidget(label("本地地址  http://127.0.0.1:11434", "section"))
        self.connect_button = button("一键连接 / 刷新 ModelDock 模型", self.connect, True)
        col.addWidget(self.connect_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.connection_info = label("连接后自动读取主项目上次选择的模型；没有主项目偏好时选择已安装模型。", "muted", True)
        col.addWidget(self.connection_info)
        row = QHBoxLayout()
        row.addWidget(label("主聊天上下文"))
        self.context_combo = QComboBox()
        self.context_combo.addItem("4096", 4096)
        self.context_combo.currentIndexChanged.connect(self.change_context)
        row.addWidget(self.context_combo)
        row.addStretch()
        col.addLayout(row)
        self.model_info = label("尚未读取模型上下文元数据。", "muted", True)
        col.addWidget(self.model_info)
        col.addWidget(label("上下文越大，占用内存 / 显存越多。计数使用 UTF-8 字节保守估算与消息开销，不冒充模型精确 tokenizer；还会预留回复和安全空间。超长历史自动摘要续聊，单条超限会保留原文并阻止发送。", "muted", True))
        box.addWidget(frame)
        frame, col = card()
        col.addWidget(label("独立 SUB 项目", "section"))
        col.addWidget(label("ModelDock 负责部署模型；ModelDock Chat 负责对话、人格、记忆和黑话知识。双方使用独立源码、仓库、配置与数据目录。", "muted", True))
        col.addWidget(button("查看主项目 ModelDock-Windows ↗", lambda: QDesktopServices.openUrl(QUrl(PARENT_URL))))
        col.addWidget(button("查看本 SUB 仓库 ↗", lambda: QDesktopServices.openUrl(QUrl("https://github.com/reaiksp/ModelDock-Chat"))))
        col.addWidget(label("本地数据目录：" + str(self.store.path.parent), "muted", True))
        col.addWidget(label("主聊天、人格、记忆与学习材料保存在本机 SQLite。外部学习 API Key 可由 Windows DPAPI 加密保存；聊天数据库本身未加密。", "muted", True))
        box.addWidget(frame)
        box.addStretch()

    def connect(self):
        if self.job:
            return
        self.connected = False
        def done(result):
            names = result["models"]
            selected = self.store.get("model", "")
            parent = result["parent_model"]
            if parent in names:
                selected = parent
            elif selected not in names:
                selected = names[0] if names else ""
            self.model_combo.blockSignals(True)
            self.model_combo.clear()
            self.model_combo.addItems(names)
            self.model_combo.setCurrentText(selected)
            self.model_combo.blockSignals(False)
            self.engine.model = selected
            self.connected = bool(selected)
            self.connection_badge.setText("●  本地模型已连接" if names else "○  服务在线，尚无本地模型")
            self.connection_info.setText(f"发现 {len(names)} 个本地模型。当前：{selected or '请先在主项目部署模型'}")
            self.status.setText("本地连接完成。" if names else "请先到 ModelDock 部署一个文字模型。")
            if selected:
                self.after_job = self.read_model_info
        self.run_job(lambda j: connect_local(j.task, lambda text: j.event.emit("status", text), self.store.path.parent), done, "connect")

    def select_model(self, name):
        if self.job or not name:
            return
        self.engine.model = name
        self.connected = True
        self.store.set("model", name)
        self.read_model_info()

    def read_model_info(self):
        if not self.engine.model or self.job:
            return
        model = self.engine.model
        # Never retain another model's larger context after a switch or failed lookup.
        self.engine.model_limit = 4096
        self.engine.context = min(self.engine.context, 4096)
        self.context_combo.blockSignals(True)
        self.context_combo.clear()
        self.context_combo.addItem("4096", 4096)
        self.context_combo.blockSignals(False)
        self.model_info.setText("正在读取模型元数据；未识别时保守按 4096。")
        def done(limit):
            self.engine.model_limit = limit
            requested = int(self.store.get("context", 4096))
            options = [x for x in [2048, 4096, 8192, 16384, 32768] if x <= limit]
            if not options:
                options = [limit]
            value = max([x for x in options if x <= requested] or [options[0]])
            self.context_combo.blockSignals(True)
            self.context_combo.clear()
            for option in options:
                self.context_combo.addItem(str(option), option)
            self.context_combo.setCurrentIndex(options.index(value))
            self.context_combo.blockSignals(False)
            self.engine.context = value
            self.model_info.setText(f"{model} · 元数据最大上下文 {limit} · 本次使用 {value}。无法识别时保守按 4096。")
            self.persona_counter()
            self.learning_counter()
            self.update_counter()
        self.run_job(lambda j: model_context(self.engine.client, model, j.task), done, "metadata")

    def change_context(self):
        if self.job or not self.context_combo.currentData():
            return
        value = min(self.context_combo.currentData(), self.engine.model_limit)
        self.engine.context = value
        self.store.set("context", value)
        self.persona_counter()
        self.learning_counter()
        self.update_counter()

    def closeEvent(self, event):
        if self.job:
            self.job.task.cancel()
        event.accept()
