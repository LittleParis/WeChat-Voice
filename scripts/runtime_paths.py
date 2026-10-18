"""Separate immutable bundled resources from persistent user data."""
from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import sys
import threading

_dll_lock = threading.RLock()
WORKERS = {"capture_desktop_key.py", "prepare_desktop_data.py"}


def frozen():
    return bool(getattr(sys, "frozen", False))


def resources():
    return Path(sys._MEIPASS).resolve() if frozen() else Path(__file__).resolve().parents[1]


def default_data_dir():
    if frozen():
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "VoiceKeep"
    return resources()


def worker_command(script, arguments):
    if script not in WORKERS:
        raise ValueError("Unsupported local worker")
    if frozen():
        return [sys.executable, "--worker", script, *map(str, arguments)]
    return [sys.executable, "-u", str(resources() / "scripts" / script), *map(str, arguments)]


def attach_worker_streams():
    # A windowed bootloader leaves sys.stdout=None even when Popen supplied pipes.
    if sys.stdout is None and os.name == "nt":
        import msvcrt
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.argtypes = [ctypes.c_ulong]
        kernel.GetStdHandle.restype = ctypes.c_void_p
        handle = kernel.GetStdHandle(ctypes.c_ulong(-11).value)
        if handle and handle != ctypes.c_void_p(-1).value:
            descriptor = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
            sys.stdout = os.fdopen(descriptor, "w", encoding="utf-8", errors="replace", buffering=1)
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = sys.stdout


@contextmanager
def system_dll_search():
    """System programs must not inherit the bundle's private DLL search path."""
    with _dll_lock:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True) if frozen() and os.name == "nt" else None
        if kernel:
            kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
            kernel.SetDllDirectoryW(None)
        try:
            yield
        finally:
            if kernel:
                kernel.SetDllDirectoryW(str(resources()))
