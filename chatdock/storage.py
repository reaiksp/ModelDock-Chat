"""Shared local model locations and verified, non-destructive migration.

Changing an environment variable alone cannot redirect a running Ollama server.
Old model files are deliberately retained after a successful directory change.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path

import psutil


def config_dir():
    return Path(os.getenv('LOCALAPPDATA', str(Path.home() / '.local/share'))) / 'ModelDockRuntime'


def settings():
    try:
        value = json.loads((config_dir() / 'storage.json').read_text('utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(key, path):
    folder = config_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'storage.json'
    value = dict(settings(), **{key: str(path)})
    temporary = folder / ('storage-' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), 'utf-8')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def text_models_dir():
    value = settings().get('text_models')
    if not value and os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                value = winreg.QueryValueEx(key, 'OLLAMA_MODELS')[0]
        except OSError:
            pass
    value = value or os.getenv('OLLAMA_MODELS') or str(Path.home() / '.ollama/models')
    return Path(os.path.expandvars(value)).expanduser().resolve()


def speech_models_dir(runtime_root):
    value = settings().get('speech_models')
    return Path(value).expanduser().resolve() if value else Path(runtime_root) / 'models'


def validate_directory(value):
    path = Path(value).expanduser()
    if not path.is_absolute() or str(path).startswith('\\\\'):
        raise ValueError('请选择本机磁盘上的绝对目录。')
    for part in [path, *path.parents]:
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('模型迁移目录不能包含符号链接或目录联接。')
    path = path.resolve()
    if path == Path(path.anchor) or path == Path.home().resolve():
        raise ValueError('请选择专门的模型子目录，不要选择磁盘根目录或用户主目录。')
    if path.exists() and not path.is_dir():
        raise ValueError('模型存储位置不是目录。')
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ('.modeldock-write-' + uuid.uuid4().hex)
    try:
        with probe.open('xb') as stream:
            stream.write(b'write-check')
    finally:
        probe.unlink(missing_ok=True)
    return path


@contextmanager
def migration_lock():
    folder = config_dir()
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'storage.lock').open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError('另一应用正在迁移模型，请稍后再试。') from exc
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _inventory(path):
    if not path.exists():
        return {}
    if path.is_symlink() or path.is_junction():
        raise ValueError('源模型目录不能是链接。')
    result = {}
    for directory, folders, files in os.walk(path, followlinks=False):
        for name in folders + files:
            item = Path(directory) / name
            if item.is_symlink() or item.is_junction():
                raise ValueError('源模型目录包含链接，未自动迁移。')
        for name in files:
            item = Path(directory) / name
            stat = item.stat()
            result[str(item.relative_to(path))] = (stat.st_size, stat.st_mtime_ns)
    return result


def file_hash(path, task):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(4 * 1024 * 1024):
            task.check()
            result.update(block)
    return result.hexdigest()


def copy_verified(source, target, task, log):
    source, target = Path(source).absolute(), Path(target).absolute()
    for path in [source, target]:
        for part in [path, *path.parents]:
            if part.is_symlink() or part.is_junction():
                raise ValueError('模型迁移路径不能包含链接。')
    source, target = source.resolve(), target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    if source == target:
        return {}
    if source in target.parents or target in source.parents:
        raise ValueError('源目录与目标目录不能相互包含。')
    inventory = _inventory(source)
    missing = sum(size for name, (size, _) in inventory.items() if not (target / name).exists())
    if shutil.disk_usage(target).free < missing + 64 * 1024**2:
        raise ValueError('目标磁盘空间不足，原模型未删除。')
    for name, signature in sorted(inventory.items()):
        task.check()
        original, destination = source / name, target / name
        log('校验并迁移：' + name)
        # Recheck every existing ancestor so a nested destination link cannot
        # redirect writes outside the selected model directory.
        for parent in [destination, *destination.parents]:
            if parent == target.parent:
                break
            if parent.is_symlink() or parent.is_junction():
                raise ValueError('目标目录包含链接，迁移已停止。')
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = file_hash(original, task)
        if destination.exists():
            if destination.stat().st_size != signature[0] or file_hash(destination, task) != digest:
                raise ValueError('目标存在不同的同名文件，未覆盖：' + name)
        else:
            created = False
            try:
                with original.open('rb') as incoming, destination.open('xb') as outgoing:
                    created = True
                    while block := incoming.read(4 * 1024 * 1024):
                        task.check()
                        outgoing.write(block)
                if file_hash(destination, task) != digest:
                    raise ValueError('迁移文件校验失败：' + name)
            except BaseException:
                if created:
                    destination.unlink(missing_ok=True)
                raise
        now = original.stat()
        if (now.st_size, now.st_mtime_ns) != signature:
            raise ValueError('源文件在迁移时发生变化，请停止下载后重试。')
    if _inventory(source) != inventory:
        raise ValueError('源模型目录在迁移时发生变化，请停止下载后重试。')
    return inventory


def _api(path, body=None):
    request = urllib.request.Request('http://127.0.0.1:11434' + path,
        data=None if body is None else json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=4) as response:
        return json.loads(response.read(4_000_001))


def _listener():
    for item in psutil.net_connections(kind='tcp'):
        if item.laddr.port == 11434 and item.status == psutil.CONN_LISTEN and item.pid:
            process = psutil.Process(item.pid)
            if Path(process.exe()).name.lower() not in {'ollama.exe', 'ollama'} or 'serve' not in process.cmdline():
                raise ValueError('本地端口由其他程序使用，未终止该程序。')
            return process
    return None


def active_text_dir():
    try:
        rows = _api('/api/tags').get('models', [])
        for row in rows[:3]:
            content = _api('/api/show', {'model': row['name']}).get('modelfile', '')
            match = re.search(r'(?m)^FROM\s+"?([^\r\n"]+)"?\s*$', content)
            if match:
                path = Path(match[1].strip())
                if path.is_absolute() and path.parent.name == 'blobs':
                    return path.parent.parent.resolve()
        process = _listener()
        if process:
            return Path(process.environ().get('OLLAMA_MODELS') or Path.home() / '.ollama/models').resolve()
    except (OSError, ValueError, RuntimeError, psutil.Error):
        pass
    return None


def check_server_directory():
    if not settings().get('text_models'):
        return
    actual = active_text_dir()
    if actual is None or actual != text_models_dir():
        raise ValueError('Ollama实际模型目录与设置不一致，请在模型存储位置中点击应用并重启服务。')


def _require_idle():
    if any(item.laddr.port == 11434 and item.status == psutil.CONN_ESTABLISHED
           for item in psutil.net_connections(kind='tcp')):
        raise ValueError('本地服务仍有进行中的请求，请先停止聊天或下载，再应用模型目录。')


def _stop(process):
    if not process:
        return
    children = process.children(recursive=True)
    process.terminate()
    process.wait(timeout=15)
    for child in children:
        try:
            child.terminate(); child.wait(timeout=10)
        except psutil.NoSuchProcess:
            pass


def _launch(cli, path):
    env = dict(os.environ, OLLAMA_MODELS=str(path), OLLAMA_HOST='127.0.0.1:11434')
    with (config_dir() / 'ollama-storage.log').open('ab') as stream:
        process = subprocess.Popen([str(cli), 'serve'], env=env, stdin=subprocess.DEVNULL,
            stdout=stream, stderr=stream, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    for _ in range(80):
        try:
            _api('/api/version')
            return process
        except (OSError, ValueError):
            if process.poll() is not None:
                break
            time.sleep(.25)
    if process.poll() is None:
        process.terminate(); process.wait(timeout=10)
    raise RuntimeError('Ollama重启失败，未发布新的模型目录。')


def _set_environment(target, log):
    os.environ['OLLAMA_MODELS'] = str(target)
    if os.name == 'nt':
        try:
            import winreg
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                winreg.SetValueEx(key, 'OLLAMA_MODELS', 0, winreg.REG_SZ, str(target))
        except OSError:
            log('用户环境变量未写入；MD/MDC已保存共享目录并直接传给服务。')


def apply_text_directory(value, task, log):
    target = validate_directory(value)
    with migration_lock():
        process = _listener()
        source = active_text_dir() if process else text_models_dir()
        if source is None:
            raise ValueError('无法确认运行中服务的实际目录，请先退出Ollama再应用。')
        cli = process.exe() if process else shutil.which('ollama')
        if not cli:
            candidate = Path(os.getenv('LOCALAPPDATA', '')) / 'Programs/Ollama/ollama.exe'
            cli = str(candidate) if candidate.is_file() else None
        if not cli:
            raise ValueError('请先安装Ollama，再应用模型目录。')
        before = {row['name']: row.get('digest') for row in _api('/api/tags').get('models', [])} if process else {}
        if process:
            _require_idle()
        inventory = copy_verified(source, target, task, log)
        task.check()
        if process:
            _require_idle()
        stopped = False
        launched = None
        try:
            if process:
                log('正在重启本地Ollama服务以应用目录…')
                _stop(process); stopped = True
            if source != target and _inventory(source) != inventory:
                raise ValueError('迁移期间源目录发生变化，原目录已保留。')
            launched = _launch(cli, target)
            after = {row['name']: row.get('digest') for row in _api('/api/tags').get('models', [])}
            if any(after.get(name) != digest for name, digest in before.items()):
                raise ValueError('新目录的模型列表未通过核对。')
            if active_text_dir() != target:
                raise ValueError('服务未使用目标目录，原目录已保留。')
            task.check()
            _save('text_models', target)
            _set_environment(target, log)
            log('模型目录已生效并校验；旧模型文件保留，未自动删除。')
            return {'source': str(source), 'target': str(target), 'models': len(after), 'copied_files': len(inventory)}
        except BaseException:
            if launched and launched.poll() is None:
                launched.terminate(); launched.wait(timeout=15)
            if stopped:
                try:
                    _launch(cli, source)
                except Exception:
                    log('原服务恢复失败，请从原目录重新启动Ollama。')
            raise


def apply_speech_directory(value, source, task, log):
    target = validate_directory(value)
    with migration_lock():
        copy_verified(source, target, task, log)
        task.check()
        _save('speech_models', target)
    return str(target)
