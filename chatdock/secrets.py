"""Windows user-scoped DPAPI. API keys never go into SQLite or plain-text files."""
import ctypes
import os
from ctypes import wintypes
from pathlib import Path


class Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("持久保存密钥仅支持 Windows；其他平台可在本次运行中使用。")
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, dest = Blob(len(data), buffer), Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    name = "CryptUnprotectData" if decrypt else "CryptProtectData"
    fn = getattr(crypt, name)
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(dest)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(dest.pbData, dest.cbData)
    finally:
        kernel.LocalFree(dest.pbData)


class Vault:
    def __init__(self, path):
        self.path = Path(path)

    def save(self, key):
        if not key:
            self.path.unlink(missing_ok=True)
            return
        encoded = _crypt(key.encode("utf-8"))
        temp = self.path.with_suffix(".tmp")
        temp.write_bytes(encoded)
        temp.replace(self.path)

    def load(self):
        if not self.path.exists():
            return ""
        return _crypt(self.path.read_bytes(), decrypt=True).decode("utf-8")
