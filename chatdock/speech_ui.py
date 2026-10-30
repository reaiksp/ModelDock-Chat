"""MDC-only speech and joint deployment page; source chat and prompts stay intact."""
import json
import shutil
import threading
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from .hardware import detect, recommend, free_disk, ollama_models_dir
from .i18n import tr
from .network import Client, connect_local
from .speech import (SpeechRecords, SpeechService, readout_text, SPEAKERS, LANGUAGES,
                     run_command)


class SpeechPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window, self.store = window, window.store
        self.service = SpeechService(self.store.path.parent)
        self.records = SpeechRecords(self.store)
        self.hw = None
        self.plan = None
        self.run_id = None
        self.run_result = {"paths": [], "metrics": []}
        self.pending_auto = None
        self.queue = []
        self.cache = self.store.path.parent / "speech_audio"
        self.cache.mkdir(exist_ok=True)
        self.audio = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.mediaStatusChanged.connect(self.media_status)
        self.player.errorOccurred.connect(lambda *args: self.info.setText(tr("音频播放失败，请检查音频设备。")))
        self.reload_settings()
        box = QVBoxLayout(self)
        box.setSpacing(12)
        title = QLabel("中文朗读与联合部署")
        title.setObjectName("section")
        box.addWidget(title)
        note = QLabel("全部功能独立运行于 MDC。开启语音会为 Qwen3-TTS 预留资源，再选择文字模型；估算不保证实际速度。")
        note.setWordWrap(True)
        box.addWidget(note)
        self.tabs = QTabWidget()
        box.addWidget(self.tabs)
        deployment = QWidget()
        readout = QWidget()
        evaluation = QWidget()
        for widget, title in [(deployment, "联合部署"), (readout, "朗读与偏好"), (evaluation, "质量与历史")]:
            self.tabs.addTab(widget, title)
        box = QVBoxLayout(deployment)
        form = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItem("不开语音 · 纯文字", False)
        self.mode.addItem("开语音 · 文字与语音联合推荐", True)
        self.mode.setCurrentIndex(int(bool(self.settings.get("enabled", False))))
        form.addRow("部署模式", self.mode)
        self.variant = QComboBox()
        for label, value in [("自动搭配", "auto"), ("Qwen3-TTS 0.6B CustomVoice", "0.6B"), ("Qwen3-TTS 1.7B CustomVoice", "1.7B")]:
            self.variant.addItem(label, value)
        self.variant.setCurrentIndex(max(0, self.variant.findData(self.settings.get("variant", "1.7B"))))
        form.addRow("语音模型", self.variant)
        self.usage = QComboBox()
        self.usage.addItem("通用对话", "chat")
        self.usage.addItem("代码开发", "code")
        self.usage.setCurrentIndex(max(0, self.usage.findData(self.settings.get("usage", "chat"))))
        form.addRow("文字模型用途", self.usage)
        self.remote_text = QCheckBox("文字聊天使用 API，本机只运行语音")
        self.remote_text.setChecked(bool(self.settings.get("remote_text", False)))
        self.remote_text.setToolTip("此选项只影响资源预算；请在主聊天选择 API 供应商。")
        form.addRow(self.remote_text)
        self.preference = QComboBox()
        for label, value in [("均衡", "balanced"), ("速度优先", "speed"), ("质量优先", "quality")]:
            self.preference.addItem(label, value)
        self.preference.setCurrentIndex(max(0, self.preference.findData(self.settings.get("preference", "quality"))))
        form.addRow("推荐偏好", self.preference)
        box.addLayout(form)
        self.hardware = QLabel("点击检测，比较开语音与不开语音两套方案。")
        self.hardware.setWordWrap(True)
        box.addWidget(self.hardware)
        self.without = QLabel()
        self.with_voice = QLabel()
        for widget in [self.without, self.with_voice]:
            widget.setWordWrap(True)
            box.addWidget(widget)
        row = QHBoxLayout()
        self.scan_button = self.button("检测与联合推荐", self.scan, row)
        self.text_deploy = self.button("下载推荐的文字模型", self.deploy_text, row)
        box.addLayout(row)
        row = QHBoxLayout()
        from PySide6.QtGui import QDesktopServices
        self.button("安装 Ollama ↗", lambda:QDesktopServices.openUrl(QUrl("https://ollama.com/download/windows")), row)
        self.button("安装 Python ↗", lambda:QDesktopServices.openUrl(QUrl("https://www.python.org/downloads/windows/")), row)
        box.addLayout(row)
        form = QFormLayout()
        self.python_path = QLineEdit(self.settings.get("python", ""))
        self.python_path.setPlaceholderText("Python 3.12 路径，留空自动检测")
        browse = QHBoxLayout()
        browse.addWidget(self.python_path)
        self.button("选择 Python", self.choose_python, browse)
        form.addRow("部署运行环境", browse)
        self.torch_wheel = QLineEdit(self.settings.get("torch_wheel", ""))
        self.torch_wheel.setPlaceholderText("可选：已有的官方 PyTorch 2.11.0 CUDA 12.8 安装包")
        wheel_row = QHBoxLayout()
        wheel_row.addWidget(self.torch_wheel)
        self.button("选择安装包", self.choose_wheel, wheel_row)
        form.addRow("复用本地依赖", wheel_row)
        self.source = QComboBox()
        self.source.addItem("ModelScope", "modelscope")
        self.source.addItem("Hugging Face", "huggingface")
        self.source.setCurrentIndex(max(0,self.source.findData(self.settings.get("source", "modelscope"))))
        form.addRow("模型下载源", self.source)
        box.addLayout(form)
        row = QHBoxLayout()
        self.install_button = self.button("一键安装语音环境与模型", self.install, row)
        box.addLayout(row)
        row = QHBoxLayout()
        self.start_button = self.button("启动 / 连接语音服务", self.start_service, row)
        self.stop_service_button = self.button("停止语音服务并释放显存", self.stop_service, row)
        box.addLayout(row)
        self.info = QLabel("首次安装会下载独立 Python 依赖与模型，需要网络及较多磁盘空间。推理仅使用本机模型。")
        self.info.setWordWrap(True)
        box.addWidget(self.info)
        box.addStretch()
        box = QVBoxLayout(readout)
        form = QFormLayout()
        self.speaker = QComboBox()
        self.speaker.addItems(SPEAKERS)
        self.speaker.setProperty("_i18n_user_items", True)
        self.speaker.setCurrentText(self.settings.get("speaker", "Vivian"))
        form.addRow("音色", self.speaker)
        self.language = QComboBox()
        for language in LANGUAGES:
            self.language.addItem(language, language)
        self.language.setCurrentIndex(max(0,self.language.findData(self.settings.get("language", "Chinese"))))
        self.language.setProperty("_i18n_skip", True)
        form.addRow("朗读语言", self.language)
        self.style = QLineEdit(self.settings.get("instruct", ""))
        self.style.setMaxLength(1000)
        self.style.setPlaceholderText("例如：温和、清晰地朗读。1.7B 支持语气指令。")
        form.addRow("个人语气偏好", self.style)
        self.rate = QDoubleSpinBox()
        self.rate.setRange(.5, 2)
        self.rate.setSingleStep(.1)
        self.rate.setValue(float(self.settings.get("rate", 1)))
        form.addRow("播放速度", self.rate)
        self.volume = QSpinBox()
        self.volume.setRange(0, 100)
        self.volume.setValue(int(self.settings.get("volume", 80)))
        form.addRow("音量", self.volume)
        box.addLayout(form)
        self.auto = QCheckBox("回复完成后自动朗读")
        self.auto.setChecked(bool(self.settings.get("auto", False)))
        box.addWidget(self.auto)
        self.skip_code = QCheckBox("朗读时跳过代码块")
        self.skip_code.setChecked(bool(self.settings.get("skip_code", True)))
        box.addWidget(self.skip_code)
        self.dictionary = QPlainTextEdit(self.settings.get("pronunciations", ""))
        self.dictionary.setPlaceholderText("发音词典：每行 词语=朗读方式，例如 yyds=永远的神。仅作用于朗读副本。")
        self.dictionary.setMaximumHeight(110)
        box.addWidget(self.dictionary)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("朗读文本预览；原始消息、人格与记忆不变。")
        self.preview.setMaximumHeight(100)
        box.addWidget(self.preview)
        row = QHBoxLayout()
        self.save_button = self.button("保存语音偏好", self.save, row)
        self.read_button = self.button("朗读最新回复", self.read_latest, row)
        self.selection_button = self.button("朗读聊天选中文字", self.read_selection, row)
        box.addLayout(row)
        row = QHBoxLayout()
        self.button("播放 / 继续", self.play, row)
        self.button("暂停", self.player.pause, row)
        self.button("停止朗读", self.stop_reading, row)
        box.addLayout(row)
        self.progress = QLabel("尚未生成音频。")
        self.progress.setWordWrap(True)
        box.addWidget(self.progress)
        box.addStretch()
        box = QVBoxLayout(evaluation)
        self.quality = QPlainTextEdit()
        self.quality.setReadOnly(True)
        self.quality.setMaximumHeight(120)
        self.quality.setPlaceholderText("客观检查显示时长、耗时、静音与削波，不自动编造自然度评分。")
        box.addWidget(self.quality)
        form = QFormLayout()
        self.scores = []
        for name in ["准确度 1–10", "自然度 1–10", "清晰度 1–10"]:
            score = QSpinBox()
            score.setRange(1, 10)
            score.setValue(5)
            self.scores.append(score)
            form.addRow(name, score)
        self.summary = QLineEdit()
        self.summary.setMaxLength(2000)
        form.addRow("试听评价概述", self.summary)
        self.analysis = QPlainTextEdit()
        self.analysis.setMaximumHeight(90)
        form.addRow("问题分析", self.analysis)
        box.addLayout(form)
        row = QHBoxLayout()
        self.button("保存本次试听评价", self.save_review, row)
        self.button("清理本地音频缓存", self.clear_cache, row)
        box.addLayout(row)
        self.history = QComboBox()
        self.history.setProperty("_i18n_user_items", True)
        self.history.currentIndexChanged.connect(self.load_run)
        box.addWidget(self.history)
        box.addStretch()
        self.refresh_history()
        for combo in [self.variant, self.usage, self.preference]:
            combo.currentIndexChanged.connect(self.update_recommendations)
        self.remote_text.toggled.connect(self.update_recommendations)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        self.rate.valueChanged.connect(self.player.setPlaybackRate)
        self.volume.valueChanged.connect(lambda value:self.audio.setVolume(value / 100))
        self.player.setPlaybackRate(self.rate.value())
        self.audio.setVolume(self.volume.value() / 100)

    @staticmethod
    def button(text, callback, layout):
        button = QPushButton(text)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def reload_settings(self):
        self.settings = self.store.get("speech", {})
        if not isinstance(self.settings, dict):
            self.settings = {}

    def snapshot(self):
        return {"enabled": self.mode.currentData(), "variant": self.variant.currentData(),
            "usage": self.usage.currentData(), "preference": self.preference.currentData(),
            "remote_text": self.remote_text.isChecked(),
            "python": self.python_path.text().strip(), "source": self.source.currentData(),
            "torch_wheel": self.torch_wheel.text().strip(),
            "speaker": self.speaker.currentText(), "language": self.language.currentData(),
            "instruct": self.style.text(), "rate": self.rate.value(), "volume": self.volume.value(),
            "auto": self.auto.isChecked(), "skip_code": self.skip_code.isChecked(),
            "pronunciations": self.dictionary.toPlainText()}

    def save(self):
        try:
            readout_text("validation", self.dictionary.toPlainText())
            self.settings = self.snapshot()
            self.store.set("speech", self.settings)
            self.info.setText("语音偏好已保存；原始聊天与人格提示词不变。")
            return True
        except ValueError as exc:
            self.window.error(str(exc))
            return False

    def busy(self, value):
        for widget in [self.scan_button, self.text_deploy, self.install_button, self.start_button,
                       self.stop_service_button, self.read_button, self.selection_button,
                       self.mode, self.variant, self.usage, self.preference, self.source, self.history, self.remote_text]:
            widget.setEnabled(not value)

    def scan(self):
        self.window.run_job(lambda j: detect(self.service.root), self.scanned, "hardware")

    def scanned(self, hw):
        self.hw = hw
        self.hardware.setText(tr("空闲显存 {0} GiB · 可用内存 {1} GiB · 语音盘 {2} GiB").format(
            f"{hw.vram:.1f}", f"{hw.available:.1f}", f"{hw.disk:.1f}"))
        self.hardware.setToolTip(hw.gpu)
        self.update_recommendations()

    def get_plan(self, voice):
        if not self.hw:
            return None
        same_disk = self.service.root.resolve().anchor.lower() == ollama_models_dir().resolve().anchor.lower()
        return recommend(self.hw, voice, self.variant.currentData(), self.usage.currentData(),
                         self.preference.currentData(), same_disk, self.remote_text.isChecked())

    def describe(self, plan):
        if not plan:
            return tr("当前配置没有安全的同时运行推荐。请释放资源，或使用 API 文字聊天。")
        voice = "Qwen3-TTS " + plan["voice"] if plan["voice"] else tr("不开语音")
        return tr("文字：{0} · 语音：{1} · 文字预算 {2} GiB · 为语音预留显存 {3} GiB / 内存 {4} GiB").format(
            plan["text"], voice, plan["budget"], plan["reserve_gpu"], plan["reserve_ram"])

    def update_recommendations(self):
        if not self.hw:
            return
        self.without.setText(tr("不开语音方案：") + self.describe(self.get_plan(False)))
        self.with_voice.setText(tr("开语音方案：") + self.describe(self.get_plan(True)))
        self.plan = self.get_plan(bool(self.mode.currentData()))
        self.text_deploy.setEnabled(not self.window.job and not self.remote_text.isChecked())
        self.store.set("speech", self.snapshot())

    def mode_changed(self):
        self.update_recommendations()
        if not self.mode.currentData():
            self.stop_reading()
            if self.service.owned and not self.window.job:
                def done(result):
                    self.info.setText(tr("语音已关闭，服务已停止；正在重新检测资源。"))
                    self.window.after_job = self.scan
                self.window.run_job(lambda j:self.service.stop(), done, "speech_stop")

    def choose_python(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("选择 Python 3.12"), "", "Python (python.exe);;All files (*)")
        if path:
            self.python_path.setText(path)

    def choose_wheel(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("选择官方 PyTorch 安装包"), "", "PyTorch wheel (*.whl)")
        if path:
            self.torch_wheel.setText(path)

    def selected_size(self):
        if self.variant.currentData() in {"0.6B", "1.7B"}:
            return self.variant.currentData()
        plan = self.get_plan(True)
        if not plan:
            raise ValueError("请先检测硬件，确认有可运行的语音搭配。")
        return plan["voice"]

    def install(self):
        if self.window.job:
            return
        if not self.save():
            return
        if not self.hw or not self.get_plan(True):
            self.window.error("请先检测硬件，确认有可运行的语音搭配。")
            return
        try:
            size = self.selected_size()
        except ValueError as exc:
            self.window.error(str(exc))
            return
        settings = self.snapshot()
        self.window.run_job(lambda j: self.service.install(size, settings["python"], settings["source"], j.task,
            lambda text:j.event.emit("speech_status", text), settings["torch_wheel"]),
            lambda result:self.info.setText(tr("语音模型安装完成，可启动服务。")), "speech_install")

    def deploy_text(self):
        if self.window.job:
            return
        if self.remote_text.isChecked():
            self.window.error("API 文字模式无需下载文字模型，请在主聊天选择供应商。")
            return
        if not self.plan:
            self.window.error("请先检测硬件并选择可运行的部署方案。")
            return
        plan = dict(self.plan)
        def work(job):
            import os
            import shutil
            result = connect_local(job.task, lambda text:job.event.emit("speech_status",text), self.store.path.parent)
            cli = shutil.which("ollama")
            if not cli:
                candidate = Path(os.getenv("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe"
                cli = str(candidate) if candidate.is_file() else None
            if not cli:
                raise ValueError("未找到 Ollama CLI，请安装 Ollama 后重试。")
            job.event.emit("speech_status", tr("正在通过 Ollama CLI 下载文字模型…"))
            # Download command uses a catalog tag, never arbitrary user shell input.
            env = os.environ.copy()
            env["OLLAMA_HOST"] = "127.0.0.1:11434"
            run_command([cli, "pull", plan["text"]], job.task, lambda text:job.event.emit("speech_status",text), env=env)
            return plan["text"]
        def done(tag):
            self.store.set("model", tag)
            self.window.engine.local_model = tag
            self.info.setText(tr("文字模型下载完成，请在连接设置刷新并选择。"))
        self.window.run_job(work, done, "text_deploy")

    def start_service(self):
        if self.window.job:
            return
        try:
            size = self.selected_size()
        except ValueError as exc:
            self.window.error(str(exc))
            return
        self.window.run_job(lambda j:self.service.start(size, j.task, lambda text:j.event.emit("speech_status",text)),
            lambda result:self.info.setText(tr("本地语音服务已连接，首次朗读会加载模型。")), "speech_start")

    def stop_service(self):
        if self.window.job:
            return
        self.stop_reading()
        self.window.run_job(lambda j:self.service.stop(), lambda result:self.info.setText(tr("语音服务已停止。")), "speech_stop")

    def read_latest(self):
        rows = self.store.messages(self.window.session_id)
        row = next((m for m in reversed(rows) if m["role"] == "assistant" and m["status"] == "complete"), None)
        if row:
            self.read_message(row["id"])
        else:
            self.window.error("当前会话没有可朗读的完整回复。")

    def read_message(self, ident):
        if self.window.job:
            self.window.status.setText(tr("请等待或停止当前任务后再操作。"))
            return
        row = next((m for m in self.store.messages(self.window.session_id) if m["id"] == ident and m["role"] == "assistant"), None)
        if row:
            self.generate(row["content"], row["id"])

    def read_selection(self):
        text = self.window.transcript.textCursor().selectedText().replace("\u2029", "\n")
        if text.strip():
            self.generate(text, None)
        else:
            self.window.error("请先在聊天记录中选中文字。")

    def generate(self, original, message_id):
        if self.window.job or not self.save():
            return
        settings = self.snapshot()
        if not settings["enabled"]:
            self.window.error("请先在部署模式中开启语音。")
            return
        try:
            readout = readout_text(original, settings["pronunciations"], settings["skip_code"])
            health = self.service.health()
            selected = settings["variant"]
            if selected != "auto" and health.get("repo", "").split("-12Hz-")[-1] != selected + "-CustomVoice":
                raise ValueError("运行中的语音模型与所选型号不同，请停止服务并启动所选模型。")
            settings["actual_repo"] = health.get("repo")
            settings["device"] = health.get("device")
            from .speech import chunks
            chunks(readout)
        except (OSError, ValueError, RuntimeError) as exc:
            self.window.error(str(exc) if isinstance(exc, ValueError) else tr("请先安装并启动本地语音服务。"))
            return
        self.player.stop()
        self.queue = []
        self.preview.setPlainText(readout)
        self.run_id = self.records.begin(self.window.session_id, message_id, original, readout, settings)
        self.run_result = {"paths": [], "metrics": []}
        self.summary.clear()
        self.analysis.clear()
        for score in self.scores:
            score.setValue(5)
        ident = self.run_id
        self.window.run_job(lambda j:self.service.generate(readout, settings, self.cache / ident, j.task, j.event.emit),
            lambda result:self.complete(ident, result), "speech_generate")
        self.progress.setText("正在分段生成语音，首次加载可能较慢…")
        if "0.6B" in str(settings.get("actual_repo")) and settings.get("instruct"):
            self.info.setText(tr("0.6B 使用预设音色，不应用语气指令；需要语气控制请选择 1.7B。"))

    def handle_event(self, kind, value):
        if self.window.job and self.window.job.task.event.is_set():
            return
        if kind == "speech_status":
            self.info.setText(str(value))
        elif kind == "speech_progress":
            index, total, path, quality = value
            self.run_result["paths"].append(path)
            self.run_result["metrics"].append(quality)
            self.records.finish(self.run_id, self.run_result, "running")
            self.progress.setText(tr("语音生成 {0} / {1} 段").format(index, total))
            self.show_quality()
            self.queue.append(path)
            if self.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState:
                self.next_audio()

    def show_quality(self):
        lines = []
        for item in self.run_result.get("metrics", []):
            lines.append(tr("第 {0} 段：时长 {1}s · 生成 {2}s · 静音 {3}% · 削波 {4}%").format(
                item["segment"], item["seconds"], item["generation_seconds"],
                round(item["silence_ratio"] * 100, 1), round(item["clipping_ratio"] * 100, 2)))
            if item["warnings"]:
                names = {"near_silence":"音量接近静音", "excess_silence":"静音比例偏高", "clipping":"削波比例偏高"}
                lines.append(tr("客观异常：") + ", ".join(tr(names.get(name, name)) for name in item["warnings"]))
        self.quality.setPlainText("\n".join(lines))

    def complete(self, ident, result):
        self.records.finish(ident, result)
        self.run_result = result
        self.progress.setText("语音生成完成，请试听后评分。")
        self.refresh_history(ident)

    def failed(self):
        if self.run_id:
            self.records.finish(self.run_id, self.run_result, "interrupted")
        self.progress.setText("语音生成已中断，已完成的分段保留，可重新生成。")
        self.player.stop()
        self.queue = []
        self.refresh_history(self.run_id)

    def media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.next_audio()

    def next_audio(self):
        if self.queue:
            path = self.queue.pop(0)
            self.player.setSource(QUrl.fromLocalFile(path))
            self.player.play()

    def play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
            self.player.play()
            return
        self.queue = [p for p in self.run_result.get("paths", []) if Path(p).is_file()]
        self.player.stop()
        self.next_audio()

    def stop_reading(self):
        self.player.stop()
        self.queue = []
        self.pending_auto = None
        if self.window.job and self.window.job_kind == "speech_generate":
            self.window.job.task.cancel()
            threading.Thread(target=self.service.cancel, daemon=True).start()

    def auto_request(self, session_id):
        if not self.auto.isChecked() or not self.mode.currentData():
            return
        rows = self.store.messages(session_id)
        row = next((m for m in reversed(rows) if m["role"] == "assistant" and m["status"] == "complete"), None)
        if row:
            self.pending_auto = (session_id, row["id"])

    def maybe_auto(self):
        if self.window.job or not self.pending_auto:
            return
        session_id, ident = self.pending_auto
        self.pending_auto = None
        if session_id == self.window.session_id and self.auto.isChecked():
            self.read_message(ident)

    def save_review(self):
        if not self.run_id or self.window.job:
            return
        try:
            self.records.review(self.run_id, *(s.value() for s in self.scores), self.summary.text(), self.analysis.toPlainText())
            self.info.setText("本次人工试听评价已保存。")
        except ValueError as exc:
            self.window.error(str(exc))

    def refresh_history(self, selected=None):
        self.history.blockSignals(True)
        self.history.clear()
        for row in self.store.all("SELECT id,created,status,readout FROM speech_runs ORDER BY created DESC LIMIT 100"):
            self.history.addItem(row["id"][:8] + " · " + row["status"] + " · " + row["readout"][:35], row["id"])
        if selected:
            self.history.setCurrentIndex(self.history.findData(selected))
        self.history.blockSignals(False)

    def load_run(self):
        if self.window.job:
            return
        ident = self.history.currentData()
        rows = self.store.all("SELECT * FROM speech_runs WHERE id=?", (ident,))
        if not rows:
            return
        row = rows[0]
        self.player.stop()
        self.queue = []
        self.run_id = ident
        self.run_result = json.loads(row["result"])
        self.preview.setPlainText(row["readout"])
        self.show_quality()
        review = self.store.all("SELECT * FROM speech_reviews WHERE run_id=?", (ident,))
        data = review[0] if review else {}
        for score, name in zip(self.scores, ["accuracy", "naturalness", "clarity"]):
            score.setValue(data.get(name, 5))
        self.summary.setText(data.get("summary", ""))
        self.analysis.setPlainText(data.get("analysis", ""))

    def clear_cache(self):
        if self.window.job:
            return
        if QMessageBox.question(self, tr("清理音频缓存"), tr("删除 MDC 生成的音频文件？聊天、偏好和评价记录会保留。")) != QMessageBox.StandardButton.Yes:
            return
        self.player.stop()
        self.player.setSource(QUrl())
        self.queue = []
        # Only unlink ordinary files under the dedicated cache; never follow directory symlinks.
        root = self.cache.resolve()
        for folder in root.iterdir():
            if folder.is_dir() and not folder.is_symlink() and folder.resolve().parent == root:
                for path in folder.iterdir():
                    if path.suffix == ".wav" and path.is_file() and not path.is_symlink() and path.resolve().parent == folder.resolve():
                        path.unlink()
                if not any(folder.iterdir()):
                    folder.rmdir()
        self.info.setText("本地音频缓存已清理，评价记录保留。")

    def shutdown(self):
        if getattr(self, "_closed", False):
            return
        self._closed = True
        self.stop_reading()
        # Windows audio backends can keep the WAV open after stop(); detach the source.
        self.player.setSource(QUrl())
        self.player.deleteLater()
        self.audio.deleteLater()
        try:
            self.store.set("speech", self.snapshot())
        finally:
            if self.service.owned:
                self.service.stop()
