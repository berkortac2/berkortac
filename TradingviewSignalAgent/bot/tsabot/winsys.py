"""Operating-system helpers for the desktop app. Windows-only features degrade to no-ops elsewhere.

* single instance (named mutex on Windows, file lock elsewhere)
* start with Windows (HKCU\\...\\Run, current user only, no admin rights)
* keep the computer awake while the bot runs (SetThreadExecutionState)
* native message box
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from . import BOT_ROOT

log = logging.getLogger("tsabot.winsys")
IS_WIN = sys.platform == "win32"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "TSABot"


class SingleInstance:
    """Only one TSA Bot may run per user: two bots on one account would trade twice."""

    def __init__(self, lock_path: Path, name: str = "Local\\TSABot-single-instance"):
        self.lock_path, self.name = Path(lock_path), name
        self._h = None
        self._f = None

    def acquire(self) -> bool:
        if IS_WIN:                                       # pragma: no cover - Windows only
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateMutexW.restype = ctypes.c_void_p
            k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
            k32.CloseHandle.argtypes = [ctypes.c_void_p]
            h = k32.CreateMutexW(None, False, self.name)
            exists = ctypes.get_last_error() == 183                     # ERROR_ALREADY_EXISTS
            if not h or exists:
                if h:
                    k32.CloseHandle(h)                                  # keep no handle: the owner's lock decides
                return False
            self._h = h
            return True
        import fcntl
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self.lock_path, "w")
        try:
            fcntl.flock(self._f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._f.close()
            self._f = None
            return False
        return True

    def release(self) -> None:
        if self._f is not None:
            self._f.close()
            self._f = None
        if self._h and IS_WIN:                           # pragma: no cover
            import ctypes
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(self._h))
            self._h = None


def launch_command(minimized: bool = True) -> str:
    """Command line that starts this app (packaged .exe or the Python sources)."""
    extra = " --minimized" if minimized else ""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"{extra}'
    exe = Path(sys.executable)
    if IS_WIN and exe.name.lower() == "python.exe" and (exe.parent / "pythonw.exe").exists():
        exe = exe.parent / "pythonw.exe"                 # no console window
    return f'"{exe}" "{BOT_ROOT / "TSABot.pyw"}"{extra}'


def set_autostart(enable: bool, command: str | None = None, winreg_mod=None) -> bool:
    """Adds / removes TSA Bot in the current user's Windows start-up list. Returns the new state."""
    if not IS_WIN and winreg_mod is None:
        return False
    wr = winreg_mod
    if wr is None:                                       # pragma: no cover
        import winreg as wr
    with wr.OpenKey(wr.HKEY_CURRENT_USER, RUN_KEY, 0, wr.KEY_SET_VALUE | wr.KEY_QUERY_VALUE) as k:
        if enable:
            wr.SetValueEx(k, RUN_NAME, 0, wr.REG_SZ, command or launch_command(True))
        else:
            try:
                wr.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass
    return is_autostart(wr)


def is_autostart(winreg_mod=None) -> bool:
    if not IS_WIN and winreg_mod is None:
        return False
    wr = winreg_mod
    if wr is None:                                       # pragma: no cover
        import winreg as wr
    try:
        with wr.OpenKey(wr.HKEY_CURRENT_USER, RUN_KEY, 0, wr.KEY_QUERY_VALUE) as k:
            wr.QueryValueEx(k, RUN_NAME)
            return True
    except FileNotFoundError:
        return False


_awake = False


def keep_awake(on: bool) -> None:
    """Stops Windows from going to sleep while the bot runs (the screen may still turn off).
    Per thread: call it from a thread that lives as long as the app."""
    global _awake
    if not IS_WIN or on == _awake:
        return
    try:                                                 # pragma: no cover
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
        _awake = on
    except Exception as e:
        log.warning("keep_awake failed: %s", e)


def message_box(text: str, title: str = "TSA Bot") -> None:
    if IS_WIN:                                           # pragma: no cover
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)      # MB_ICONINFORMATION
            return
        except Exception:
            pass
    print(f"{title}: {text}", file=sys.stderr if sys.stderr else None)


def create_shortcut(target_cmd: str, name: str = "TSA Bot") -> bool:
    """Desktop shortcut (Windows, via PowerShell's WScript.Shell COM object)."""
    if not IS_WIN:
        return False
    import shlex
    import subprocess
    parts = shlex.split(target_cmd, posix=False)
    exe, args = parts[0].strip('"'), " ".join(parts[1:])
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop" / f"{name}.lnk"
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:TSA_LNK);$s.TargetPath=$env:TSA_EXE;"
          "$s.Arguments=$env:TSA_ARGS;$s.WorkingDirectory=$env:TSA_DIR;$s.IconLocation=$env:TSA_EXE;$s.Save()")
    env = {**os.environ, "TSA_LNK": str(desktop), "TSA_EXE": exe, "TSA_ARGS": args.replace("--minimized", "").strip(),
           "TSA_DIR": str(Path(exe).parent)}
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env,
                       capture_output=True, creationflags=0x08000000)          # CREATE_NO_WINDOW
    return r.returncode == 0
