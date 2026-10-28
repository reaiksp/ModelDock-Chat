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
from .i18n import LanguageManager, tr
from .providers import load_providers, save_providers, validate_provider
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
        self.profiles = load_providers(self.store)
        self.provider_keys = {}
        self.provider_edit_id = None
        self.local_models = []
        self.active_source = "local"
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
        side.addWidget(label("Language / 语言", "sideMuted"))
        self.language_combo = QComboBox()
        self.language_combo.addItem("简体中文", "zh_CN")
        self.language_combo.addItem("English", "en")
        self.language_combo.setProperty('_i18n_skip', True)
        side.addWidget(self.language_combo)
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
        self.status = label("可连接本机 Ollama，或手动选择 API 供应商。", "muted", True)
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
        self.refresh_sources()
        self.refresh_personas()
        self.refresh_sessions()
        self.refresh_memory()
        self.refresh_knowledge()
        self.render_chat()
        self.navigate(0)
        self.prompt.setPlainText(self.store.get("draft:"+str(self.session_id), ""))
        self.update_counter()
        for combo in [self.model_combo, self.persona_combo, self.persona_editor_combo, self.provider_picker]:
            combo.setProperty('_i18n_user_items', True)
        self.chat_source.setProperty('_i18n_routes', True)
        self.learning_mode.setProperty('_i18n_routes', True)
        self.i18n = LanguageManager(self, self.store.get('ui_language','zh_CN'), lambda lang:self.store.set('ui_language',lang))
        self.language_combo.setCurrentIndex(max(0,self.language_combo.findData(self.i18n.language)))
        self.language_combo.currentIndexChanged.connect(lambda: self.i18n.set_language(self.language_combo.currentData()))
        self.i18n.changed.connect(self.language_changed)
        self.i18n.apply()
        saved = self.store.session(self.session_id).get("route_id", "local")
        if saved != "local" and self.chat_source.findData(saved) >= 0:
            self.restoring_session = True
            try:
                self.chat_source.setCurrentIndex(self.chat_source.findData(saved))
            finally:
                self.restoring_session = False
        elif saved != "local":
            self.connected = False
            self.route_notice.setText("原供应商已删除。请重新选择供应商以新建会话。")
        elif auto_connect:
            QTimer.singleShot(100, self.connect)

    def save_reply_language(self):
        self.store.set('response_language',self.reply_language.currentData())
        self.update_counter()

    def save_draft(self):
        self.store.set("draft:"+str(self.session_id),self.prompt.toPlainText())

    def language_changed(self, language):
        blocked = self.language_combo.blockSignals(True)
        self.language_combo.setCurrentIndex(self.language_combo.findData(language))
        self.language_combo.blockSignals(blocked)
        self.save_draft()
        position = self.transcript.verticalScrollBar().value()
        self.render_chat()
        self.transcript.verticalScrollBar().setValue(position)

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
        for widget in [self.chat_source, self.provider_picker, self.provider_new, self.provider_delete]:
            widget.setEnabled(not busy)
        self.model_combo.setEnabled(not busy and self.engine.backend == "local")
        self.variant_button.setEnabled(not busy and self.engine.backend == "local")
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
            self.retrieval.setText("知识参考：" + (tr(str(value)) if value == "本轮未命中黑话词条" else str(value)))
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
        self.chat_source = QComboBox()
        self.chat_source.addItem("本机 Ollama", "local")
        self.chat_source.currentIndexChanged.connect(self.change_source)
        row.addWidget(self.chat_source, 1)
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
        source_row = QHBoxLayout()
        self.route_notice = label("本机模式：聊天内容在本机处理。", "muted", True)
        source_row.addWidget(self.route_notice, 1)
        source_row.addWidget(button("管理 API 供应商", self.open_provider_settings))
        box.addLayout(source_row)
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
        self.draft_timer = QTimer(self)
        self.draft_timer.setSingleShot(True)
        self.draft_timer.timeout.connect(self.save_draft)
        self.prompt.textChanged.connect(lambda:self.draft_timer.start(400))
        box.addWidget(self.prompt)
        self.counter = label("", "counter", True)
        box.addWidget(self.counter)
        row = QHBoxLayout()
        self.auto_memory = QCheckBox("回复后用当前模型整理三维记忆")
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
        self.save_draft()
        self.draft_timer.stop()
        self.session_id = item.data(Qt.ItemDataRole.UserRole)
        previous = self.prompt.blockSignals(True)
        self.prompt.setPlainText(self.store.get("draft:"+str(self.session_id),""))
        self.prompt.blockSignals(previous)
        session = self.store.session(self.session_id)
        source = session.get("route_id", "local")
        target = self.chat_source.findData(source)
        if target < 0:
            self.connected = False
            self.route_notice.setText("此会话原供应商已删除。请选择供应商并新建会话后再发送。")
        elif source != self.active_source:
            self.restoring_session = True
            try:
                self.chat_source.setCurrentIndex(target)
            finally:
                self.restoring_session = False
        persona_id = session["persona_id"]
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
        self.save_draft()
        self.draft_timer.stop()
        ident = persona_id or self.persona_combo.currentData() or 1
        self.session_id = self.store.new_session(ident, route_id=self.active_source)
        self.connected = bool(self.engine.model)
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
        blocked = self.prompt.blockSignals(True)
        self.prompt.setPlainText(self.store.get("draft:"+str(self.session_id), ""))
        self.prompt.blockSignals(blocked)
        self.refresh_sessions()
        self.select_session(self.session_list.currentItem())

    def render_chat(self):
        rows = self.store.messages(self.session_id)
        content = []
        summary = self.store.session(self.session_id)["summary"]
        if summary:
            content.append(tr("<p style='color:#68877b'>已从上一会话携带摘要，完整记录仍保存在原会话。</p>"))
        for row in rows:
            who = "你" if row["role"] == "user" else "助手"
            flag = "" if row["status"] in {"complete", "pending"} else " · 已中断"
            content.append(self.bubble(who + flag, row["content"], row["role"] == "user"))
        if self.pending_text:
            content.append(self.bubble("你", self.pending_text, True))
        if self.streaming:
            content.append(self.bubble("助手 · 正在生成", self.streaming))
        if not content:
            content.append(tr("<div style='color:#7a9289;padding:45px 20px'><h2>按你喜欢的方式，慢慢聊。</h2><p>连接本地模型或选择 API 供应商，选一个人格，就可以开始了。</p><p>记忆和黑话在确认后，会自动成为下一轮对话的参考。</p></div>"))
        self.transcript.setHtml("<body style='font-family:Microsoft YaHei UI;font-size:13px'>" + "".join(content) + "</body>")
        bar = self.transcript.verticalScrollBar()
        bar.setValue(bar.maximum())

    @staticmethod
    def bubble(who, text, user=False):
        bg = "#e0f0e7" if user else "#ffffff"
        value = html.escape(text).replace("\n", "<br>")
        return f"<table width='100%' cellpadding='14' cellspacing='0' style='margin:8px 0;background:{bg};'><tr><td><b style='color:#397c67'>{html.escape(tr(who))}</b><br><br>{value}</td></tr></table><br>"

    def update_counter(self):
        if not hasattr(self, "counter"):
            return
        try:
            stats = self.engine.preview(self.session_id, self.prompt.toPlainText())
            warn = ""
            if stats["too_long"]:
                warn = tr(" ⚠ 单条输入超限，请缩短后发送。")
            elif stats["rollover"]:
                warn = tr(" ⚠ 历史将自动整理为摘要，并新开续聊会话。")
            elif stats["used"] > stats["limit"] * .85:
                warn = tr(" 接近上下文上限。")
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
            self.error("请先连接本地模型或配置聊天 API 供应商。")
            return
        self.store.set("draft:"+str(self.session_id), "")
        self.draft_timer.stop()
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
        self.analyze_button = button("用当前模型分析三维记忆", self.analyze_memory)
        row.addWidget(self.analyze_button)
        row.addWidget(button("手动添加", self.add_memory))
        box.addLayout(row)
        self.memory_table = self.table(["概述", "评价 1–10", "分析", "状态", "原文证据"])
        box.addWidget(self.memory_table)
        row = QHBoxLayout()
        row.addWidget(button("确认选中记忆", lambda: self.approve_items("memories", self.memory_table)))
        row.addWidget(button("编辑选中", self.edit_memory))
        row.addWidget(button("删除选中", lambda: self.delete_items("memories", self.memory_table)))
        row.addStretch()
        box.addLayout(row)
        box.addWidget(label("评分表示记忆的长期参考价值，不是对用户的评价。分析使用当前聊天模型；API 模式会把当前会话的用户消息发送给该供应商。候选确认后才参与聊天。", "muted", True))
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
        self.fill_table(self.memory_table, self.store.items("memories"), ["summary", "score", "analysis", "status", "evidence"])

    def save_profile(self):
        text = self.user_profile.toPlainText().strip()
        if estimate(text) > 500:
            self.error("用户资料超过 500 个 UTF-8 字节，请缩短。原文已保留。")
            return
        self.store.set("user_profile", text)
        self.update_counter()
        self.status.setText("用户资料已保存。")

    def add_memory(self):
        self.edit_memory(new=True)

    def edit_memory(self, checked=False, new=False):
        ids = self.selected_ids(self.memory_table)
        if not new and len(ids) != 1:
            self.status.setText("请只选择一条记忆。")
            return
        row = {} if new else self.store.all("SELECT * FROM memories WHERE id=?", (ids[0],))[0]
        dialog = QDialog(self)
        dialog.setWindowTitle("三维记忆 · 保存后待确认")
        dialog.resize(560, 440)
        form = QFormLayout(dialog)
        score = QSpinBox()
        score.setRange(1, 10)
        score.setValue(row.get("score", 5))
        summary, analysis = QPlainTextEdit(), QPlainTextEdit()
        summary.setPlainText(row.get("summary", ""))
        analysis.setPlainText(row.get("analysis", ""))
        form.addRow("评价 1–10（长期参考价值）", score)
        form.addRow("概述", summary)
        form.addRow("分析与适用场景", analysis)
        form.addRow("原文证据", label(row.get("evidence", "手动记忆"), "muted", True))
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        controls.accepted.connect(dialog.accept)
        controls.rejected.connect(dialog.reject)
        form.addRow(controls)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            if new:
                self.store.add_memory(summary.toPlainText(), score=score.value(), summary=summary.toPlainText(), analysis=analysis.toPlainText())
            else:
                self.store.update_memory(ids[0], summary.toPlainText(), score.value(), analysis.toPlainText())
            self.refresh_memory()
            self.update_counter()
        except ValueError as exc:
            self.error(str(exc))

    def analyze_memory(self):
        if self.job:
            return
        if not self.connected:
            self.error("请先连接模型或选择 API 供应商。")
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
        self.learning_mode.addItem("跟随主聊天供应商（独立学习会话）", "active")
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
        self.provider_picker = QComboBox()
        self.provider_picker.currentIndexChanged.connect(self.load_provider_editor)
        form.addRow("已保存供应商", self.provider_picker)
        actions = QHBoxLayout()
        self.provider_new = button("新增供应商", self.new_provider)
        self.provider_delete = button("删除当前供应商", self.delete_provider)
        actions.addWidget(self.provider_new)
        actions.addWidget(self.provider_delete)
        form.addRow(actions)
        self.provider_name = QLineEdit()
        self.provider_name.setPlaceholderText("手动命名，如个人供应商、工作供应商")
        form.addRow("供应商名称", self.provider_name)
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
        self.provider_stream = QCheckBox("启用聊天 SSE 流式输出")
        self.provider_stream.setChecked(True)
        form.addRow("", self.provider_stream)
        self.provider_token_field = QComboBox()
        self.provider_token_field.addItems(["max_tokens", "max_completion_tokens"])
        form.addRow("输出预算参数", self.provider_token_field)
        self.provider_reasoning = QComboBox()
        self.provider_reasoning.addItems(["默认（不发送）", "none", "low", "medium", "high"])
        form.addRow("reasoning_effort（按需）", self.provider_reasoning)
        form.addRow("", self.remember_key)
        self.provider_save = button("保存供应商（聊天 / 学习可选）", self.save_provider, True)
        form.addRow("", self.provider_save)
        form.addRow(label("支持 OpenAI 兼容 /chat/completions。手动填写地址、模型和上下文，可保存多个供应商。主聊天 API 会接收人格、已确认记忆、知识和会话；学习 API 仅接收学习材料。", "muted", True))
        tabs.addTab(provider, "聊天 / 学习供应商")
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
        ident = self.learning_mode.currentData()
        profile = next((p for p in self.profiles if p["id"] == ident), None)
        context = profile["context"] if profile else (4096 if ident == "local" else self.engine.budget.context)
        size = max(100, min(5000, Budget(context, 1024).input_limit - 1100))
        self.learning_limit.setText(f"材料 {count} UTF-8 字节 · 独立上下文 {context} · 预计 {max(1, (count+size-1)//size)} 段 · 总量上限 200,000 字节。")
        self.learn_button.setEnabled(not self.job and 0 < count <= 200000)

    def provider(self):
        config = validate_provider({"id": self.provider_edit_id, "name": self.provider_name.text(),
            "base": self.provider_base.text(), "model": self.provider_model.text(),
            "context": self.provider_context.value(), "stream": self.provider_stream.isChecked(),
            "token_field": self.provider_token_field.currentText(),
            "reasoning": "" if self.provider_reasoning.currentIndex() == 0 else self.provider_reasoning.currentText()})
        key = self.provider_key.text().strip()
        Client(config["base"], key)
        return dict(config, key=key)

    def save_provider(self):
        if self.job:
            return
        try:
            config = self.provider()
            ident = config["id"]
            self.provider_vault(ident).save(config["key"] if self.remember_key.isChecked() else "")
            self.provider_keys[ident] = config["key"]
            self.profiles = [p for p in self.profiles if p["id"] != ident] + [validate_provider(config)]
            save_providers(self.store, self.profiles)
            self.provider_edit_id = ident
            self.refresh_sources()
            if self.active_source == ident:
                self.change_source()
            self.status.setText("供应商已保存，可在主聊天和黑话学习分别选择。")
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
            source = self.learning_mode.currentData()
            if source == "local":
                provider = {"backend": "local", "model": self.engine.local_model or (self.local_models[0] if self.local_models else ""), "context": 4096}
            elif source == "active":
                provider = None
                if not self.connected:
                    raise ValueError("请先连接主聊天模型或选择独立学习供应商。")
            else:
                provider = self.provider_config(source)
        except (ValueError, OSError, RuntimeError) as exc:
            self.error(str(exc))
            return
        def done(result):
            self.status.setText(f"学习完成：{result['sessions']} 个独立对话，{result['count']} 条待确认词条。")
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

    def provider_vault(self, ident):
        if ident == "legacy-learning":
            return self.vault
        return Vault(self.store.path.parent / ("provider-" + ident + ".dpapi"))

    def provider_config(self, ident):
        profile = next((p for p in self.profiles if p["id"] == ident), None)
        if not profile:
            raise ValueError("供应商不存在，请先保存配置。")
        if ident not in self.provider_keys:
            self.provider_keys[ident] = self.provider_vault(ident).load()
        return dict(profile, key=self.provider_keys[ident])

    def refresh_sources(self):
        selected = self.chat_source.currentData() or "local"
        learning = self.learning_mode.currentData() or "active"
        self.chat_source.blockSignals(True)
        self.chat_source.clear()
        self.chat_source.addItem("本机 Ollama", "local")
        self.learning_mode.blockSignals(True)
        self.learning_mode.clear()
        self.learning_mode.addItem("跟随主聊天供应商（独立会话）", "active")
        self.learning_mode.addItem("本机 Ollama（独立会话）", "local")
        self.provider_picker.blockSignals(True)
        self.provider_picker.clear()
        for profile in self.profiles:
            ident, name = profile["id"], profile["name"]
            self.chat_source.addItem(name, ident)
            self.learning_mode.addItem(name, ident)
            self.provider_picker.addItem(name, ident)
        self.chat_source.setCurrentIndex(max(0, self.chat_source.findData(selected)))
        self.learning_mode.setCurrentIndex(max(0, self.learning_mode.findData(learning)))
        self.provider_picker.setCurrentIndex(max(0, self.provider_picker.findData(self.provider_edit_id)))
        for combo in [self.chat_source, self.learning_mode, self.provider_picker]:
            combo.blockSignals(False)
        if self.profiles:
            self.load_provider_editor()
        else:
            self.new_provider()
        self.learning_counter()

    def load_provider_editor(self):
        ident = self.provider_picker.currentData()
        if not ident:
            return
        try:
            config = self.provider_config(ident)
        except (OSError, RuntimeError) as exc:
            self.status.setText("供应商密钥无法读取，请重新输入。")
            config = dict(next(p for p in self.profiles if p["id"] == ident), key="")
        self.provider_edit_id = ident
        self.provider_name.setText(config["name"])
        self.provider_base.setText(config["base"])
        self.provider_model.setText(config["model"])
        self.provider_key.setText(config["key"])
        self.provider_context.setValue(config["context"])
        self.provider_stream.setChecked(config["stream"])
        self.provider_token_field.setCurrentText(config["token_field"])
        self.provider_reasoning.setCurrentIndex(max(0, self.provider_reasoning.findText(config["reasoning"])))
        self.remember_key.setChecked(self.provider_vault(ident).path.exists())

    def new_provider(self):
        if self.job:
            return
        self.provider_edit_id = None
        self.provider_name.clear()
        self.provider_base.clear()
        self.provider_model.clear()
        self.provider_key.clear()
        self.provider_context.setValue(8192)
        self.provider_stream.setChecked(True)
        self.provider_token_field.setCurrentText("max_tokens")
        self.provider_reasoning.setCurrentIndex(0)
        self.remember_key.setChecked(False)

    def delete_provider(self):
        if self.job or not self.provider_edit_id:
            return
        ident = self.provider_edit_id
        if QMessageBox.question(self, "删除供应商", "删除此供应商配置与本机保存的 Key？聊天记录保留。") != QMessageBox.StandardButton.Yes:
            return
        self.provider_vault(ident).save("")
        self.provider_keys.pop(ident, None)
        self.profiles = [p for p in self.profiles if p["id"] != ident]
        save_providers(self.store, self.profiles)
        self.provider_edit_id = None
        was_active = self.active_source == ident
        self.refresh_sources()
        if was_active:
            self.change_source()

    def open_provider_settings(self):
        self.navigate(3)
        self.learning_tabs.setCurrentIndex(1)

    def change_source(self):
        if self.job:
            return
        ident = self.chat_source.currentData() or "local"
        changed = ident != self.active_source
        try:
            if ident == "local":
                self.engine.use_local(self.engine.local_model or (self.local_models[0] if self.local_models else ""))
                self.connected = bool(self.engine.model)
                self.model_combo.blockSignals(True)
                self.model_combo.clear()
                self.model_combo.addItems(self.local_models)
                self.model_combo.setCurrentText(self.engine.model)
                self.model_combo.blockSignals(False)
                self.route_notice.setText("本机模式：聊天、记忆分析在本机处理。")
                self.connection_badge.setText("● 本机模型" if self.connected else "○ 请一键连接本机模型")
            else:
                config = self.provider_config(ident)
                self.engine.use_provider(config)
                self.connected = True
                self.model_combo.blockSignals(True)
                self.model_combo.clear()
                self.model_combo.addItem(config["model"])
                self.model_combo.setCurrentIndex(0)
                self.model_combo.blockSignals(False)
                self.route_notice.setText("API 模式：人格、已确认记忆、检索词条和本会话将发送给 " + config["name"] + "。")
                self.connection_badge.setText("API 已选择：" + config["name"])
            self.active_source = ident
            self.store.set("chat_source", ident)
            if not getattr(self, "restoring_session", False):
                if changed and self.store.messages(self.session_id):
                    self.new_session()
                else:
                    self.store.execute("UPDATE sessions SET route_id=? WHERE id=?", (ident, self.session_id))
            self.context_combo.blockSignals(True)
            self.context_combo.clear()
            choices = [n for n in [2048, 4096, 8192, 16384, 32768, 65536, 131072, 262144] if n <= self.engine.model_limit]
            if self.engine.model_limit not in choices:
                choices.append(self.engine.model_limit)
            for value in sorted(choices):
                self.context_combo.addItem(str(value), value)
            self.context_combo.setCurrentIndex(self.context_combo.findData(self.engine.context))
            self.context_combo.blockSignals(False)
            self.model_info.setText("API 上下文按供应商配置值检查。" if ident != "local" else "本机模型上下文可通过一键连接刷新。")
            self.set_busy(False)
        except (ValueError, OSError, RuntimeError) as exc:
            self.connected = False
            self.status.setText("供应商配置不可用，请重新填写：" + str(exc))
        self.update_counter()

    def build_connection(self):
        box = self.page("COMPANION CONNECTION", "与 ModelDock，一键接上", "可一键连接本地模型，也可在主聊天手动选择 API 供应商。")
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
        reply_row = QHBoxLayout()
        reply_row.addWidget(label("AI 回复语言"))
        self.reply_language = QComboBox()
        for title, value in [("跟随用户输入", "auto"), ("简体中文", "zh_CN"), ("English", "en")]:
            self.reply_language.addItem(title, value)
        self.reply_language.setCurrentIndex(max(0,self.reply_language.findData(self.store.get('response_language','auto'))))
        self.reply_language.currentIndexChanged.connect(self.save_reply_language)
        reply_row.addWidget(self.reply_language)
        col.addLayout(reply_row)
        col.addWidget(label("切换界面不会改写草稿、人格、记忆或回复语言偏好。" , "muted", True))
        col.addWidget(label("上下文越大，占用内存 / 显存越多。计数使用 UTF-8 字节保守估算与消息开销，不冒充模型精确 tokenizer；还会预留回复和安全空间。超长历史自动摘要续聊，单条超限会保留原文并阻止发送。", "muted", True))
        box.addWidget(frame)
        frame, col = card()
        col.addWidget(label("独立 SUB 项目", "section"))
        col.addWidget(label("ModelDock 负责部署模型；ModelDock Chat 负责对话、人格、记忆和黑话知识。双方使用独立源码、仓库、配置与数据目录。", "muted", True))
        col.addWidget(button("查看主项目 ModelDock-Windows ↗", lambda: QDesktopServices.openUrl(QUrl(PARENT_URL))))
        col.addWidget(button("查看本 SUB 仓库 ↗", lambda: QDesktopServices.openUrl(QUrl("https://github.com/reaiksp/ModelDock-Chat"))))
        col.addWidget(label("本地数据目录：" + str(self.store.path.parent), "muted", True))
        col.addWidget(label("主聊天、人格、记忆与学习材料保存在本机 SQLite。供应商 API Key 可由 Windows DPAPI 分别加密保存；聊天数据库本身未加密。", "muted", True))
        box.addWidget(frame)
        box.addStretch()

    def connect(self):
        if self.job:
            return
        if self.active_source == "local":
            self.connected = False
        def done(result):
            names = result["models"]
            selected = self.store.get("model", "")
            parent = result["parent_model"]
            if parent in names:
                selected = parent
            elif selected not in names:
                selected = names[0] if names else ""
            if self.active_source != "local":
                self.local_models = names
                self.engine.local_model = selected
                self.status.setText("本机模型列表已刷新；主聊天仍使用选定 API。")
                return
            self.model_combo.blockSignals(True)
            self.model_combo.clear()
            self.model_combo.addItems(names)
            self.model_combo.setCurrentText(selected)
            self.model_combo.blockSignals(False)
            self.local_models = names
            self.engine.local_model = selected
            self.engine.use_local(selected)
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
        if self.engine.backend != "local":
            return
        self.engine.model = name
        self.engine.local_model = name
        self.connected = True
        self.store.set("model", name)
        self.read_model_info()

    def read_model_info(self):
        if not self.engine.model or self.job or self.engine.backend != "local":
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
        self.save_draft()
        self.draft_timer.stop()
        if self.job:
            self.job.task.cancel()
        event.accept()
