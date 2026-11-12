"""Beginner-friendly paired-audio editor over the local training manager."""
import threading
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer, QUrl, QEvent
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QFileDialog, QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QComboBox, QSpinBox, QPlainTextEdit, QMessageBox, QInputDialog)

from .i18n import tr
from .voice_dataset import audio_rows, apply_transcripts, validate_rows, import_batch
from .voice_training import VoiceTrainingManager


class VoiceTrainingPanel(QWidget):
    finished = Signal(object, object)

    def __init__(self, speech):
        super().__init__()
        self.speech = speech
        self.manager = None  # No project/data created merely by opening the app.
        self.running = False
        self._callback = None
        self._thread = None
        self.output = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.output)
        box = QVBoxLayout(self)
        note = QLabel('同一说话人的多段音频，每段对应准确文本。先添加音频，编辑表格，再保存数据并训练。TXT 与音频同名可自动配对；不自动猜测转写。')
        note.setWordWrap(True)
        box.addWidget(note)
        row = QHBoxLayout()
        self.buttons = []
        self.button('批量添加音频…', self.choose_audio, row)
        self.button('导入文本 CSV…', self.choose_csv, row)
        self.button('移除选中行', self.remove_rows, row)
        self.button('试听选中音频', self.audition, row)
        box.addLayout(row)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(['音频文件', '对应文本（可编辑）'])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(160)
        box.addWidget(self.table)
        form = QFormLayout()
        self.project = QLineEdit('my_voice')
        self.project.setMaxLength(64)
        form.addRow('训练项目名', self.project)
        self.reference = QSpinBox()
        self.reference.setRange(1, 9999)
        form.addRow('参考音频行号（建议 10–30 秒，至少 2 秒）', self.reference)
        defaults = speech.store.get('voice_training_tools', {})
        defaults = defaults if isinstance(defaults, dict) else {}
        root = speech.service.models_dir
        python = speech.service.python
        self.python_path = QLineEdit(defaults.get('python', str(python)))
        self.scripts = QLineEdit(defaults.get('scripts', str(root / 'Qwen3-TTS-finetuning')))
        self.base_path = QLineEdit(defaults.get('base', str(root / '0.6B-Base')))
        for title, widget, directory in [('训练 Python', self.python_path, False),
                                         ('官方 finetuning 脚本目录', self.scripts, True),
                                         ('Base 模型目录', self.base_path, True)]:
            line = QHBoxLayout()
            line.addWidget(widget)
            self.button('选择…', lambda checked=False, w=widget, d=directory: self.choose_path(w, d), line)
            form.addRow(title, line)
        self.base = QComboBox()
        self.base.addItems(['0.6b', '1.7b'])
        form.addRow('Base 大小（必须与目录一致）', self.base)
        self.epochs = QSpinBox()
        self.epochs.setRange(1, 50)
        self.epochs.setValue(3)
        form.addRow('训练轮数', self.epochs)
        box.addLayout(form)
        row = QHBoxLayout()
        self.button('检查并保存训练数据', self.save_dataset, row)
        self.button('开始训练', self.start_training, row)
        self.button('打开项目目录', self.open_project, row)
        box.addLayout(row)
        row = QHBoxLayout()
        self.stop_button = QPushButton('停止训练')
        self.stop_button.clicked.connect(self.stop_training)
        row.addWidget(self.stop_button)
        self.button('试听训练结果…', self.evaluate, row)
        box.addLayout(row)
        self.status = QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setMaximumHeight(130)
        self.status.setPlainText(tr('训练仅在本机运行，不上传录音。首次需准备 Base 权重和官方训练依赖；自定义音色训练不等于学会语气指令。训练前请停止朗读及其他占用显存的模型。'))
        box.addWidget(self.status)
        self.finished.connect(self.done)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(2500)
        speech.window.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Close:
            if self.running:
                self.status.setPlainText(tr('正在保存数据或准备训练，请等待完成后再退出。'))
                event.ignore()
                return True
            self.player.stop()
            self.player.setSource(QUrl())
        return super().eventFilter(watched, event)

    def button(self, text, callback, layout):
        button = QPushButton(text)
        button.clicked.connect(callback)
        layout.addWidget(button)
        self.buttons.append(button)
        return button

    def choose_path(self, widget, directory):
        path = (QFileDialog.getExistingDirectory(self, tr('选择目录'), widget.text()) if directory else
                QFileDialog.getOpenFileName(self, tr('选择训练 Python'), widget.text(), 'Python (python.exe);;All files (*)')[0])
        if path:
            widget.setText(path)

    def rows(self):
        return [{'audio': self.table.item(i, 0).data(Qt.ItemDataRole.UserRole),
                 'text': self.table.item(i, 1).text().strip()} for i in range(self.table.rowCount())]

    def set_rows(self, rows):
        self.table.setRowCount(0)
        for row in rows:
            i = self.table.rowCount()
            self.table.insertRow(i)
            item = QTableWidgetItem(Path(row['audio']).name)
            item.setData(Qt.ItemDataRole.UserRole, row['audio'])
            item.setToolTip(row['audio'])
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(i, 0, item)
            self.table.setItem(i, 1, QTableWidgetItem(row['text']))
        self.reference.setMaximum(max(1, len(rows)))

    def choose_audio(self):
        paths, _ = QFileDialog.getOpenFileNames(self, tr('批量选择同一说话人的音频'), '', 'Audio (*.wav *.flac *.mp3 *.ogg *.m4a);;All files (*)')
        if paths:
            try:
                existing = self.rows()
                known = {row['audio'] for row in existing}
                self.set_rows(existing + [row for row in audio_rows(paths) if row['audio'] not in known])
            except Exception as exc:
                self.status.setPlainText(str(exc))

    def choose_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, tr('CSV 表头：audio,text'), '', 'CSV (*.csv)')
        if path:
            try:
                self.set_rows(apply_transcripts(self.rows(), path))
            except Exception as exc:
                self.status.setPlainText(str(exc))

    def remove_rows(self):
        selected = {i.row() for i in self.table.selectedIndexes()}
        self.set_rows([row for i, row in enumerate(self.rows()) if i not in selected])

    def audition(self):
        i = self.table.currentRow()
        if i >= 0:
            self.player.setSource(QUrl.fromLocalFile(self.rows()[i]['audio']))
            self.player.play()

    def get_manager(self):
        if self.running:
            raise ValueError('正在处理训练任务，请稍候。')
        root = self.speech.store.path.parent / 'speech' / 'training'
        name = self.project.text().strip()
        if not self.manager:
            self.manager = VoiceTrainingManager(root=root, active_project=name)
        elif name != self.manager.active_name:
            if self.manager.project_file(name).is_file():
                self.manager.use_project(name)
            else:
                self.manager.create_project(name)
        self.manager.tts_python = Path(self.python_path.text().strip())
        self.manager.finetuning_dir = Path(self.scripts.text().strip())
        self.manager.base_paths[self.base.currentText()] = Path(self.base_path.text().strip())
        return self.manager

    def submit(self, action, callback=None):
        if self.running:
            return
        self.running = True
        self._callback = callback
        for widget in self.buttons + [self.table, self.project, self.python_path, self.scripts, self.base_path, self.base, self.epochs, self.reference]:
            widget.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.player.stop()
        self.player.setSource(QUrl())
        self.status.setPlainText(tr('正在处理，请稍候…'))
        def work():
            try:
                self.finished.emit(action(), None)
            except Exception as exc:
                self.finished.emit(None, str(exc))
        self._thread = threading.Thread(target=work, daemon=True)
        self._thread.start()

    def done(self, result, error):
        self.running = False
        for widget in self.buttons + [self.table, self.project, self.python_path, self.scripts, self.base_path, self.base, self.epochs, self.reference, self.stop_button]:
            widget.setEnabled(True)
        self.status.setPlainText(str(error) if error else tr('操作完成。') + '\n' + str(result))
        if not error and self._callback:
            self._callback(result)

    def save_dataset(self):
        try:
            rows = self.rows()
            validate_rows(rows)
            manager = self.get_manager()
            index = self.reference.value() - 1
            self.submit(lambda: import_batch(manager, rows, index), lambda _: self.set_rows([]))
        except Exception as exc:
            self.status.setPlainText(str(exc))

    def start_training(self):
        try:
            if self.table.rowCount():
                raise ValueError('表格还有未保存音频，请先检查并保存训练数据。')
            manager = self.get_manager()
            if not manager.clips():
                raise ValueError('项目还没有训练数据。')
            if self.speech.service.health():
                raise ValueError('请先在联合部署页面停止语音服务，释放显存。')
            if QMessageBox.question(self, tr('开始本机训练'), tr('训练可能需要较长时间。请先停止其他文字模型及 K 语音服务；继续？')) != QMessageBox.StandardButton.Yes:
                return
            tools = {'python': str(manager.tts_python), 'scripts': str(manager.finetuning_dir), 'base': self.base_path.text().strip()}
            self.speech.store.set('voice_training_tools', tools)
            base, epochs = self.base.currentText(), self.epochs.value()
            self.submit(lambda: manager.start_training(base=base, epochs=epochs))
        except Exception as exc:
            self.status.setPlainText(str(exc))

    def stop_training(self):
        if self.manager and not self.running:
            self.submit(self.manager.stop_training)

    def poll(self):
        if self.manager and not self.running:
            try:
                state = self.manager.training_status()
                if state.get('status') in {'running', 'completed', 'failed', 'stopped'}:
                    self.status.setPlainText(str(state.get('status')) + '\n' + self.manager.tail_log(12))
            except Exception:
                pass

    def open_project(self):
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.get_manager().project_dir())))
        except Exception as exc:
            self.status.setPlainText(str(exc))

    def evaluate(self):
        try:
            manager = self.get_manager()
            text, ok = QInputDialog.getText(self, tr('试听训练结果'), tr('输入测试文本'))
            if ok and text.strip():
                self.submit(lambda: manager.evaluate(text=text.strip()), self.play_result)
        except Exception as exc:
            self.status.setPlainText(str(exc))

    def play_result(self, result):
        self.player.setSource(QUrl.fromLocalFile(result['output']))
        self.player.play()
