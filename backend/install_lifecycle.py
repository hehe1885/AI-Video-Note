"""An installation-scoped Windows event for graceful uninstaller shutdown."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import hashlib
import ntpath
import threading


def shutdown_event_name(directory):
    normalized = ntpath.normpath(str(directory)).removeprefix('\\\\?\\').lower()
    digest = hashlib.sha256(normalized.encode('utf-8')).hexdigest()
    return 'Local\\AI-Video-Note-Shutdown-' + digest


class ShutdownListener:
    def __init__(self, directory, callback):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateEventW.restype = wintypes.HANDLE
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateEventW(None, True, False, shutdown_event_name(directory))
        if not self.handle: raise ctypes.WinError(ctypes.get_last_error())
        self.stopping = threading.Event()
        def listen():
            while not self.stopping.is_set():
                result = self.kernel.WaitForSingleObject(self.handle, 200)
                if result == 0:
                    callback()
                    return
                if result != 258: return  # WAIT_TIMEOUT
        self.thread = threading.Thread(target=listen, name='installation-shutdown', daemon=True)
        self.thread.start()

    def close(self):
        self.stopping.set()
        self.thread.join(2)
        if not self.thread.is_alive() and self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def listen_for_shutdown(directory, callback):
    return ShutdownListener(directory, callback)
