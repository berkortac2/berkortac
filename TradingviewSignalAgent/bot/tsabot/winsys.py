"""Operating-system helpers for the desktop app. Windows-only features degrade to no-ops elsewhere.

* single instance (named mutex on Windows, file lock elsewhere)
* start with Windows (HKCU\\...\\Run, current user only, no admin rights)
* keep the computer awake while the bot runs (power request, shows in `powercfg /requests`)
* never let Windows throttle the bot when its window is hidden (EcoQoS opt-out)
* power source (mains / battery) for a warning in the UI
* native message box
"""
from __future__ import annotations

import logging
import os
import sys
import threading
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


def autostart_command(winreg_mod=None) -> str | None:
    """The command Windows runs for TSA Bot at sign-in, or None when it is not registered."""
    if not IS_WIN and winreg_mod is None:
        return None
    wr = winreg_mod
    if wr is None:                                       # pragma: no cover
        import winreg as wr
    try:
        with wr.OpenKey(wr.HKEY_CURRENT_USER, RUN_KEY, 0, wr.KEY_QUERY_VALUE) as k:
            return str(wr.QueryValueEx(k, RUN_NAME)[0])
    except FileNotFoundError:
        return None


def program_location() -> str:
    """Folder of the running copy: the .exe folder, or the bot folder of a source installation."""
    return str(Path(sys.executable).parent if getattr(sys, "frozen", False) else BOT_ROOT)


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


class KeepAwake:
    """Keeps Windows from idling into sleep while the bot runs (the screen may still turn off).

    A power request object is used instead of SetThreadExecutionState: it is not tied to the calling
    thread (events arrive on different threads), it is listed with its reason in `powercfg /requests`,
    and ExecutionRequired also stops process lifetime management from suspending the app.
    Windows still sleeps when the user closes the lid, presses the power button or picks Sleep, and on
    battery (Modern Standby) it drops the request 5 minutes after the sleep timeout."""
    REASON = "TSA Bot: Binance pozisyonları izleniyor"
    SYSTEM_REQUIRED, EXECUTION_REQUIRED = 1, 3          # POWER_REQUEST_TYPE
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001

    def __init__(self, kernel32=None):
        self._k = kernel32
        self._h = None
        self._lock = threading.Lock()                    # events and the state sync arrive on different threads
        self._exec = False
        self.on = False
        self.method: str | None = None                  # "power_request" | "thread_state" | None

    def _kernel(self):
        if self._k is None:                              # pragma: no cover - Windows only
            import ctypes
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.PowerCreateRequest.restype = ctypes.c_void_p
            k.PowerCreateRequest.argtypes = [ctypes.c_void_p]
            k.PowerSetRequest.argtypes = [ctypes.c_void_p, ctypes.c_int]
            k.PowerClearRequest.argtypes = [ctypes.c_void_p, ctypes.c_int]
            k.SetThreadExecutionState.restype = ctypes.c_uint
            k.SetThreadExecutionState.argtypes = [ctypes.c_uint]
            self._k = k
        return self._k

    def _create(self, k):
        import ctypes

        class REASON_CONTEXT(ctypes.Structure):
            _fields_ = [("Version", ctypes.c_ulong), ("Flags", ctypes.c_ulong), ("Reason", ctypes.c_wchar_p),
                        ("_pad", ctypes.c_void_p * 3)]          # rest of the union (detailed form)
        self._ctx = REASON_CONTEXT(0, 0x1, self.REASON)         # POWER_REQUEST_CONTEXT_SIMPLE_STRING
        h = k.PowerCreateRequest(ctypes.byref(self._ctx))
        return None if h in (None, 0, -1, 2 ** 64 - 1) else h

    def set(self, on: bool) -> bool:
        """Returns whether the computer is now kept awake."""
        on = bool(on)
        with self._lock:
            return self._set(on)

    def _set(self, on: bool) -> bool:
        if on == self.on:
            return self.on
        try:
            k = self._kernel()
            if on:
                if self._h is None:
                    self._h = self._create(k)
                if self._h is not None and k.PowerSetRequest(self._h, self.SYSTEM_REQUIRED):
                    self._exec = bool(k.PowerSetRequest(self._h, self.EXECUTION_REQUIRED))
                    self.method = "power_request"
                else:                                    # very old Windows: per-thread fallback
                    k.SetThreadExecutionState(self.ES_CONTINUOUS | self.ES_SYSTEM_REQUIRED)
                    self.method = "thread_state"
            else:
                if self.method == "power_request":
                    if self._exec:
                        k.PowerClearRequest(self._h, self.EXECUTION_REQUIRED)
                    k.PowerClearRequest(self._h, self.SYSTEM_REQUIRED)
                elif self.method == "thread_state":
                    k.SetThreadExecutionState(self.ES_CONTINUOUS)
                self.method, self._exec = None, False
            self.on = on
        except Exception as e:
            log.warning("keep awake failed: %s", e)
        return self.on


_keeper = KeepAwake()


def keep_awake(on: bool) -> bool:
    """Process-wide; safe to call from any thread. No-op outside Windows."""
    if not IS_WIN:
        return False
    return _keeper.set(on)


def awake_state() -> dict:
    return {"on": _keeper.on, "method": _keeper.method}


def disable_power_throttling(kernel32=None) -> bool:
    """Opts the process out of EcoQoS and of the timer-resolution throttling Windows 11 applies to apps whose
    window is hidden or minimized: the bot must keep its 5-minute rhythm while it sits in the tray."""
    if not IS_WIN and kernel32 is None:
        return False
    try:
        import ctypes

        class PPTS(ctypes.Structure):                    # PROCESS_POWER_THROTTLING_STATE
            _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]
        k = kernel32
        if k is None:                                    # pragma: no cover - Windows only
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.GetCurrentProcess.restype = ctypes.c_void_p
            k.SetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
        # ControlMask = EXECUTION_SPEED | IGNORE_TIMER_RESOLUTION, StateMask = 0 -> both mechanisms off
        st = PPTS(1, 0x1 | 0x4, 0)
        ok = bool(k.SetProcessInformation(k.GetCurrentProcess(), 4, ctypes.byref(st), ctypes.sizeof(st)))
        if not ok:
            log.warning("power throttling opt-out refused")
        return ok
    except Exception as e:
        log.warning("power throttling opt-out failed: %s", e)
        return False


def power_status(kernel32=None) -> dict:
    """{"ac": True (mains) / False (battery) / None (unknown), "battery": percent or None}."""
    if not IS_WIN and kernel32 is None:
        return {"ac": None, "battery": None}
    try:
        import ctypes

        class SPS(ctypes.Structure):                     # SYSTEM_POWER_STATUS
            _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                        ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                        ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
        k = kernel32 if kernel32 is not None else ctypes.windll.kernel32      # pragma: no cover
        st = SPS()
        if not k.GetSystemPowerStatus(ctypes.byref(st)):
            return {"ac": None, "battery": None}
        no_battery = st.BatteryFlag == 128 or st.BatteryFlag == 255
        ac = {0: False, 1: True}.get(st.ACLineStatus)
        return {"ac": ac, "battery": None if no_battery or st.BatteryLifePercent > 100 else int(st.BatteryLifePercent)}
    except Exception as e:
        log.warning("power status failed: %s", e)
        return {"ac": None, "battery": None}


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
