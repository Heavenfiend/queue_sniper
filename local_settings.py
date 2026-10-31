"""Настройки и учётные данные текущего пользователя Windows."""
import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import tempfile

DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "QSniper"


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def crypt(data: bytes, decrypt=False) -> bytes:
    if os.name != "nt":
        raise OSError("Сохранение пароля поддерживается только в Windows")
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                         ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    # Без LOCAL_MACHINE: расшифровка привязана к пользователю Windows
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OSError("Windows не удалось обработать сохранённые данные входа")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.memset(target.data, 0, target.size)
        kernel.LocalFree(target.data)


def write_atomic(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_credentials(email: str, password: str):
    email = email.strip()
    if not email or not password:
        raise ValueError("Почта и пароль не могут быть пустыми")
    payload = json.dumps([email, password], ensure_ascii=False).encode("utf-8")
    write_atomic(DATA_DIR / "account.bin", crypt(payload))


def load_credentials():
    path = DATA_DIR / "account.bin"
    if not path.exists():
        return None
    try:
        data = json.loads(crypt(path.read_bytes(), decrypt=True).decode("utf-8"))
        if not isinstance(data, list) or len(data) != 2 or not all(isinstance(x, str) and x for x in data):
            raise ValueError()
        return tuple(data)
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError("Не удалось прочитать сохранённый вход. Введи его заново в пункте 3.") from exc


def forget_credentials():
    (DATA_DIR / "account.bin").unlink(missing_ok=True)


def parse_interval(value) -> float:
    interval = float(str(value).replace(",", "."))
    if not math.isfinite(interval) or interval < 1:
        raise ValueError("Интервал должен быть числом не меньше 1 секунды")
    return interval


def load_interval() -> float:
    path = DATA_DIR / "settings.json"
    if not path.exists():
        return 5
    try:
        return parse_interval(json.loads(path.read_text(encoding="utf-8"))["interval"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("Не удалось прочитать интервал; временно используются 5 секунд") from exc


def save_interval(value):
    interval = parse_interval(value)
    write_atomic(DATA_DIR / "settings.json", json.dumps({"interval": interval}).encode())
    return interval
