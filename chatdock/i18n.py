"""Runtime UI localization. Editable values and user records are never rewritten."""
import json
import re
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QObject, QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QAbstractButton, QComboBox, QDialog,
    QFileDialog, QGroupBox, QInputDialog, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QTableWidget, QTabWidget, QTextEdit, QWidget)

SOURCE_ROLE = int(Qt.ItemDataRole.UserRole) + 101
_current = None


def source_text(combo):
    return combo.itemData(combo.currentIndex(), SOURCE_ROLE) or combo.currentText()


def tr(text):
    return _current.text(text) if _current else text


def response_policy(language='auto'):
    if language == 'en':
        return 'USER REPLY-LANGUAGE SETTING: ENGLISH. Write the reply in English. This explicit preference overrides language defaults in the persona and previous messages. Chinese input alone is not a request to change language. Only switch if the latest user message explicitly asks for another language.'
    if language == 'zh_CN':
        return 'USER REPLY-LANGUAGE SETTING: SIMPLIFIED CHINESE. Write the reply in Simplified Chinese. This explicit preference overrides persona and history language defaults. Only switch if the latest user message explicitly asks for another language.'
    return 'Follow explicit user language requests and confirmed language preferences; otherwise reply in the language of the current user message. UI language does not determine reply language.'


def adapt_user_message(text, language='auto'):
    if language == 'en':
        return text + "\n\n[Application preference: reply in English unless this user message explicitly requests a different language.]"
    if language == 'zh_CN':
        return text + "\n\n[Application preference: reply in Simplified Chinese unless this user message explicitly requests a different language.]"
    return text


class LanguageManager(QObject):
    changed = Signal(str)

    def __init__(self, root, language='zh_CN', save=None):
        super().__init__(root)
        global _current
        _current = self
        self.root = root
        self.save = save
        path = Path(__file__).with_name('translations.json')
        self.catalog = json.loads(path.read_text('utf-8')) if path.exists() else {}
        self.patterns = []
        for source, target in self.catalog.items():
            fields = list(re.finditer(r'\{(\d+)\}', source))
            if not fields:
                continue
            expression, end = '', 0
            for field in fields:
                expression += re.escape(source[end:field.start()]) + '(.*?)'
                end = field.end()
            expression += re.escape(source[end:])
            self.patterns.append((re.compile('^'+expression+'$', re.S), [f.group(1) for f in fields], target))
        self.patterns.sort(key=lambda p: len(p[0].pattern), reverse=True)
        self.language = language if language in {'zh_CN','en'} else 'zh_CN'
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.apply)
        self.timer.start(250)
        QApplication.instance().installEventFilter(self)
        self.install_dialog_wrappers()

    @lru_cache(maxsize=4096)
    def text(self, value):
        if not isinstance(value, str) or self.language != 'en':
            return value
        if value in self.catalog:
            return self.catalog[value]
        for pattern, fields, translated in self.patterns:
            match = pattern.fullmatch(value)
            if match:
                values = dict(zip(fields, match.groups()))
                if '运行约需' in pattern.pattern and '预算' in pattern.pattern:
                    values = {k: {'显存':'VRAM','内存':'RAM'}.get(v,v) for k,v in values.items()}
                return re.sub(r'\{(\d+)\}', lambda m: values.get(m.group(1),m.group(0)), translated)
        for source, translated in self.catalog.items():
            if source.endswith(('：',':')) and value.startswith(source):
                return translated.rstrip()+ ' ' + value[len(source):]
        return value

    def set_language(self, language):
        self.language = language if language in {'zh_CN','en'} else 'zh_CN'
        self.text.cache_clear()
        if self.save:
            self.save(self.language)
        self.apply()
        self.changed.emit(self.language)

    def value(self, widget, key, current, setter):
        original = widget.property('_i18n_source_'+key)
        rendered = widget.property('_i18n_rendered_'+key)
        if original is None or current != rendered:
            original = current
            widget.setProperty('_i18n_source_'+key, original)
        translated = self.text(original)
        if current != translated:
            setter(translated)
        widget.setProperty('_i18n_rendered_'+key, translated)

    def apply_widget(self, widget):
        if widget.property('_i18n_skip'):
            return
        if isinstance(widget,(QMainWindow,QDialog)):
            self.value(widget,'title',widget.windowTitle(),widget.setWindowTitle)
        if isinstance(widget,(QLabel,QAbstractButton)):
            self.value(widget,'text',widget.text(),widget.setText)
        if isinstance(widget,QGroupBox):
            self.value(widget,'group',widget.title(),widget.setTitle)
        if isinstance(widget,(QLineEdit,QPlainTextEdit,QTextEdit,QComboBox)):
            self.value(widget,'placeholder',widget.placeholderText(),widget.setPlaceholderText)
        if widget.toolTip():
            self.value(widget,'tip',widget.toolTip(),widget.setToolTip)
        if isinstance(widget,QMessageBox):
            self.value(widget,'message',widget.text(),widget.setText)
            self.value(widget,'info',widget.informativeText(),widget.setInformativeText)
        if widget.property('_i18n_plain_ui') and isinstance(widget,(QPlainTextEdit,QTextEdit)):
            self.value(widget,'plain',widget.toPlainText(),widget.setPlainText)
        if isinstance(widget,QComboBox) and not widget.property('_i18n_user_items'):
            previous = widget.blockSignals(True)
            try:
                for index in range(widget.count()):
                    if widget.property('_i18n_routes') and widget.itemData(index) not in {'local','active'}:
                        continue
                    current = widget.itemText(index)
                    source = widget.itemData(index,SOURCE_ROLE)
                    rendered = widget.itemData(index,SOURCE_ROLE+1)
                    if source is None or current != rendered:
                        source=current
                        widget.setItemData(index,source,SOURCE_ROLE)
                    translated=self.text(source)
                    if current != translated:
                        widget.setItemText(index,translated)
                    widget.setItemData(index,translated,SOURCE_ROLE+1)
            finally:
                widget.blockSignals(previous)
        if isinstance(widget,QTabWidget):
            for index in range(widget.count()):
                self.value(widget,'tab'+str(index),widget.tabText(index),lambda text,i=index:widget.setTabText(i,text))
        if isinstance(widget,QTableWidget):
            for index in range(widget.columnCount()):
                item=widget.horizontalHeaderItem(index)
                if item:
                    self.value(widget,'header'+str(index),item.text(),item.setText)
                    if widget.property('_i18n_source_header'+str(index)) == '状态':
                        for row in range(widget.rowCount()):
                            cell=widget.item(row,index)
                            if not cell: continue
                            source=cell.data(SOURCE_ROLE)
                            rendered=cell.data(SOURCE_ROLE+1)
                            if source is None or cell.text()!=rendered:
                                source=cell.text()
                                cell.setData(SOURCE_ROLE,source)
                            value=self.text(source)
                            cell.setText(value)
                            cell.setData(SOURCE_ROLE+1,value)

    def apply(self):
        app=QApplication.instance()
        if not app:
            return
        for top in app.topLevelWidgets():
            if top is not self.root and top.windowType() == Qt.WindowType.ToolTip:
                continue
            for widget in [top]+top.findChildren(QWidget):
                try:
                    self.apply_widget(widget)
                except RuntimeError:
                    pass

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show:
            QTimer.singleShot(0,self.apply)
        return False

    @staticmethod
    def install_dialog_wrappers():
        if getattr(QMessageBox,'_md_i18n_wrapped',False):
            return
        for cls,names in [(QMessageBox,['warning','critical','information','question']),
                          (QInputDialog,['getText','getMultiLineText','getInt']),
                          (QFileDialog,['getSaveFileName','getOpenFileName'])]:
            for name in names:
                original=getattr(cls,name)
                def wrapped(*args,_original=original,_cls=cls,**kwargs):
                    values=list(args)
                    for i in ([1] if _cls is QFileDialog else [1,2]):
                        if len(values)>i and isinstance(values[i],str):
                            values[i]=tr(values[i])
                    keys = ['title','label'] if _cls is QInputDialog else ['title','caption','label','text']
                    for key in keys:
                        if key in kwargs and isinstance(kwargs[key],str):
                            kwargs[key]=tr(kwargs[key])
                    return _original(*values,**kwargs)
                setattr(cls,name,staticmethod(wrapped))
        QMessageBox._md_i18n_wrapped=True
