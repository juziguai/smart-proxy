"""Windows Service host for smart-proxy watchdog."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
import threading
import time


SERVICE_NAME = "SmartProxyWatchdog"
DISPLAY_NAME = "Smart Proxy Watchdog"
DESCRIPTION = (
    "Keeps smart-proxy on 127.0.0.1:8889 and the Antigravity TLS relay "
    "on 127.0.0.1:443 healthy."
)
ROOT_DIR = Path(__file__).resolve().parents[1]
ENTRY_SCRIPT = ROOT_DIR / "smart-proxy-service.py"
WATCHDOG_SCRIPT = ROOT_DIR / "smart-proxy-watchdog.ps1"
LOG_DIR = ROOT_DIR / "logs"
SERVICE_LOG = LOG_DIR / "smart-proxy-service.log"
POWERSHELL_EXE = (
    Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if "SystemRoot" in os.environ
    else Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
)

NO_ERROR = 0
INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0 = 0
STILL_ACTIVE = 259
SW_HIDE = 0
STARTF_USESHOWWINDOW = 0x00000001
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
SERVICE_ACCEPT_STOP = 0x00000001
SERVICE_ACCEPT_SHUTDOWN = 0x00000004
SERVICE_CONTROL_STOP = 0x00000001
SERVICE_CONTROL_SHUTDOWN = 0x00000005
SERVICE_WIN32_OWN_PROCESS = 0x00000010
SERVICE_STOPPED = 0x00000001
SERVICE_START_PENDING = 0x00000002
SERVICE_STOP_PENDING = 0x00000003
SERVICE_RUNNING = 0x00000004
RESTART_DELAY_SECONDS = 5


class SERVICE_STATUS(ctypes.Structure):
    _fields_ = [
        ("dwServiceType", wintypes.DWORD),
        ("dwCurrentState", wintypes.DWORD),
        ("dwControlsAccepted", wintypes.DWORD),
        ("dwWin32ExitCode", wintypes.DWORD),
        ("dwServiceSpecificExitCode", wintypes.DWORD),
        ("dwCheckPoint", wintypes.DWORD),
        ("dwWaitHint", wintypes.DWORD),
    ]


SERVICE_MAIN = ctypes.WINFUNCTYPE(None, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR))
HANDLER_EX = ctypes.WINFUNCTYPE(
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.LPVOID,
)


class SERVICE_TABLE_ENTRY(ctypes.Structure):
    _fields_ = [
        ("lpServiceName", wintypes.LPWSTR),
        ("lpServiceProc", SERVICE_MAIN),
    ]


class STARTUPINFO(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
userenv = ctypes.WinDLL("userenv", use_last_error=True)
wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)

advapi32.StartServiceCtrlDispatcherW.argtypes = [
    ctypes.POINTER(SERVICE_TABLE_ENTRY)
]
advapi32.StartServiceCtrlDispatcherW.restype = wintypes.BOOL
advapi32.RegisterServiceCtrlHandlerExW.argtypes = [
    wintypes.LPCWSTR,
    HANDLER_EX,
    wintypes.LPVOID,
]
advapi32.RegisterServiceCtrlHandlerExW.restype = wintypes.HANDLE
advapi32.SetServiceStatus.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(SERVICE_STATUS),
]
advapi32.SetServiceStatus.restype = wintypes.BOOL
advapi32.CreateProcessAsUserW.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCWSTR,
    wintypes.LPWSTR,
    wintypes.LPVOID,
    wintypes.LPVOID,
    wintypes.BOOL,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.LPCWSTR,
    ctypes.POINTER(STARTUPINFO),
    ctypes.POINTER(PROCESS_INFORMATION),
]
advapi32.CreateProcessAsUserW.restype = wintypes.BOOL

kernel32.WTSGetActiveConsoleSessionId.argtypes = []
kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateProcess.restype = wintypes.BOOL
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD

wtsapi32.WTSQueryUserToken.argtypes = [wintypes.ULONG, ctypes.POINTER(wintypes.HANDLE)]
wtsapi32.WTSQueryUserToken.restype = wintypes.BOOL

userenv.CreateEnvironmentBlock.argtypes = [
    ctypes.POINTER(wintypes.LPVOID),
    wintypes.HANDLE,
    wintypes.BOOL,
]
userenv.CreateEnvironmentBlock.restype = wintypes.BOOL
userenv.DestroyEnvironmentBlock.argtypes = [wintypes.LPVOID]
userenv.DestroyEnvironmentBlock.restype = wintypes.BOOL

_service_status_handle = None
_stop_event = threading.Event()
_runner = None
_checkpoint = 1
_service_main_callback = None
_handler_callback = None


def log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    with SERVICE_LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def win_error(prefix: str) -> OSError:
    error_code = ctypes.get_last_error()
    return ctypes.WinError(error_code, prefix)


def run_command(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        args,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"{' '.join(args)} failed: {detail}")
    return result


def build_watchdog_command() -> str:
    return (
        f'"{POWERSHELL_EXE}" '
        f'-NoProfile -ExecutionPolicy Bypass -File "{WATCHDOG_SCRIPT}"'
    )


def build_service_command() -> str:
    return f'"{sys.executable}" "{ENTRY_SCRIPT}" runservice'


def close_handle(handle) -> None:
    if handle:
        kernel32.CloseHandle(handle)


def active_console_session_id() -> int:
    session_id = kernel32.WTSGetActiveConsoleSessionId()
    if session_id == 0xFFFFFFFF:
        raise RuntimeError("no active console session")
    return int(session_id)


def set_service_status(state: int, win32_exit_code: int = 0, wait_hint: int = 0) -> None:
    global _checkpoint
    if not _service_status_handle:
        return

    controls = 0 if state in (SERVICE_START_PENDING, SERVICE_STOPPED) else (
        SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN
    )
    checkpoint = 0 if state in (SERVICE_RUNNING, SERVICE_STOPPED) else _checkpoint
    if checkpoint:
        _checkpoint += 1

    status = SERVICE_STATUS(
        SERVICE_WIN32_OWN_PROCESS,
        state,
        controls,
        win32_exit_code,
        0,
        checkpoint,
        wait_hint,
    )
    if not advapi32.SetServiceStatus(_service_status_handle, ctypes.byref(status)):
        raise win_error("SetServiceStatus")


class WatchdogRunner:
    def __init__(self) -> None:
        self.process_handle = None
        self.thread_handle = None
        self.watchdog_pid = None

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            self.ensure_running()
            stop_event.wait(5)

    def ensure_running(self) -> None:
        if self.process_handle:
            exit_code = self.get_exit_code()
            if exit_code == STILL_ACTIVE:
                return

            log(f"watchdog exited with code {exit_code}; restarting")
            self.clear_process_handles()
            time.sleep(RESTART_DELAY_SECONDS)

        try:
            self.start_in_active_user_session()
        except Exception as exc:
            log(f"watchdog launch failed: {exc}")

    def get_exit_code(self) -> int:
        exit_code = wintypes.DWORD(0)
        if not kernel32.GetExitCodeProcess(self.process_handle, ctypes.byref(exit_code)):
            raise win_error("GetExitCodeProcess")
        return int(exit_code.value)

    def start_in_active_user_session(self) -> None:
        if not WATCHDOG_SCRIPT.exists():
            raise FileNotFoundError(f"watchdog script not found: {WATCHDOG_SCRIPT}")
        if not POWERSHELL_EXE.exists():
            raise FileNotFoundError(f"powershell.exe not found: {POWERSHELL_EXE}")

        session_id = active_console_session_id()
        token = wintypes.HANDLE()
        environment = wintypes.LPVOID()
        try:
            if not wtsapi32.WTSQueryUserToken(session_id, ctypes.byref(token)):
                raise win_error("WTSQueryUserToken")
            if not userenv.CreateEnvironmentBlock(ctypes.byref(environment), token, False):
                raise win_error("CreateEnvironmentBlock")

            startup = STARTUPINFO()
            startup.cb = ctypes.sizeof(STARTUPINFO)
            startup.lpDesktop = "winsta0\\default"
            startup.dwFlags = STARTF_USESHOWWINDOW
            startup.wShowWindow = SW_HIDE

            process_info = PROCESS_INFORMATION()
            command = ctypes.create_unicode_buffer(build_watchdog_command())
            flags = CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT
            if not advapi32.CreateProcessAsUserW(
                token,
                None,
                command,
                None,
                None,
                False,
                flags,
                environment,
                str(ROOT_DIR),
                ctypes.byref(startup),
                ctypes.byref(process_info),
            ):
                raise win_error("CreateProcessAsUserW")

            self.process_handle = process_info.hProcess
            self.thread_handle = process_info.hThread
            self.watchdog_pid = int(process_info.dwProcessId)
            log(f"watchdog started in session {session_id}, PID {self.watchdog_pid}")
        finally:
            if environment:
                userenv.DestroyEnvironmentBlock(environment)
            close_handle(token)

    def stop(self) -> None:
        if not self.process_handle:
            return
        try:
            if self.get_exit_code() == STILL_ACTIVE:
                log(f"terminating watchdog PID {self.watchdog_pid}")
                kernel32.TerminateProcess(self.process_handle, 0)
                kernel32.WaitForSingleObject(self.process_handle, 5000)
        except Exception as exc:
            log(f"failed to stop watchdog PID {self.watchdog_pid}: {exc}")
        finally:
            self.clear_process_handles()

    def clear_process_handles(self) -> None:
        close_handle(self.thread_handle)
        close_handle(self.process_handle)
        self.thread_handle = None
        self.process_handle = None
        self.watchdog_pid = None


def service_control_handler(control, event_type, event_data, context):
    if control in (SERVICE_CONTROL_STOP, SERVICE_CONTROL_SHUTDOWN):
        log("stop requested")
        set_service_status(SERVICE_STOP_PENDING, wait_hint=10000)
        _stop_event.set()
        if _runner is not None:
            _runner.stop()
        return NO_ERROR
    return NO_ERROR


def service_main(argc, argv):
    global _service_status_handle, _runner
    _service_status_handle = advapi32.RegisterServiceCtrlHandlerExW(
        SERVICE_NAME,
        _handler_callback,
        None,
    )
    if not _service_status_handle:
        raise win_error("RegisterServiceCtrlHandlerExW")

    log("service starting")
    set_service_status(SERVICE_START_PENDING, wait_hint=10000)
    _runner = WatchdogRunner()
    set_service_status(SERVICE_RUNNING)
    try:
        _runner.run(_stop_event)
    except Exception as exc:
        log(f"service loop failed: {exc}")
        set_service_status(SERVICE_STOPPED, win32_exit_code=1)
        return
    finally:
        _runner.stop()
    log("service stopped")
    set_service_status(SERVICE_STOPPED)


def service_exists() -> bool:
    result = run_command(["sc.exe", "query", SERVICE_NAME], check=False)
    return result.returncode == 0


def install_service() -> None:
    if not ENTRY_SCRIPT.exists():
        raise FileNotFoundError(f"service entry script not found: {ENTRY_SCRIPT}")

    bin_path = build_service_command()
    if service_exists():
        run_command(["sc.exe", "config", SERVICE_NAME, "binPath=", bin_path])
        run_command(["sc.exe", "config", SERVICE_NAME, "start=", "delayed-auto"])
        run_command(["sc.exe", "description", SERVICE_NAME, DESCRIPTION])
        print(f"[service] updated {SERVICE_NAME}")
    else:
        run_command(
            [
                "sc.exe",
                "create",
                SERVICE_NAME,
                "binPath=",
                bin_path,
                "start=",
                "delayed-auto",
                "DisplayName=",
                DISPLAY_NAME,
            ]
        )
        run_command(["sc.exe", "description", SERVICE_NAME, DESCRIPTION])
        print(f"[service] installed {SERVICE_NAME}")

    run_command(
        [
            "sc.exe",
            "failure",
            SERVICE_NAME,
            "reset=",
            "86400",
            "actions=",
            "restart/5000/restart/5000/restart/5000",
        ]
    )
    run_command(["sc.exe", "failureflag", SERVICE_NAME, "1"], check=False)


def remove_service() -> None:
    if not service_exists():
        print(f"[service] {SERVICE_NAME} is not installed")
        return
    run_command(["sc.exe", "stop", SERVICE_NAME], check=False)
    time.sleep(2)
    run_command(["sc.exe", "delete", SERVICE_NAME])
    print(f"[service] removed {SERVICE_NAME}")


def start_service() -> None:
    run_command(["sc.exe", "start", SERVICE_NAME])
    print(f"[service] started {SERVICE_NAME}")


def stop_service() -> None:
    run_command(["sc.exe", "stop", SERVICE_NAME])
    print(f"[service] stopped {SERVICE_NAME}")


def restart_service() -> None:
    run_command(["sc.exe", "stop", SERVICE_NAME], check=False)
    time.sleep(2)
    start_service()


def print_status() -> None:
    if not service_exists():
        print(f"[service] {SERVICE_NAME} is not installed")
        return
    result = run_command(["sc.exe", "query", SERVICE_NAME], check=False)
    state = "UNKNOWN"
    for line in result.stdout.splitlines():
        if "STATE" in line:
            state = line.strip()
            break
    print(f"[service] {SERVICE_NAME}: {state}")


def run_service() -> None:
    global _service_main_callback, _handler_callback
    _stop_event.clear()
    _handler_callback = HANDLER_EX(service_control_handler)
    _service_main_callback = SERVICE_MAIN(service_main)
    service_table = (SERVICE_TABLE_ENTRY * 2)()
    service_table[0].lpServiceName = SERVICE_NAME
    service_table[0].lpServiceProc = _service_main_callback
    service_table[1].lpServiceName = None
    service_table[1].lpServiceProc = SERVICE_MAIN()
    if not advapi32.StartServiceCtrlDispatcherW(service_table):
        raise win_error("StartServiceCtrlDispatcherW")


def print_usage() -> None:
    print(
        "usage: python smart-proxy-service.py "
        "install|remove|start|stop|restart|status|runservice"
    )


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0].lower() if args else "status"
    if command == "runservice":
        run_service()
    elif command == "install":
        install_service()
    elif command in {"remove", "uninstall"}:
        remove_service()
    elif command == "start":
        start_service()
    elif command == "stop":
        stop_service()
    elif command == "restart":
        restart_service()
    elif command == "status":
        print_status()
    else:
        print_usage()
        raise SystemExit(2)


if __name__ == "__main__":
    main()
