"""
Application Entry Point for Lenovo ThinkBook Fan Control (14 G6+ IMH)
Features:
- Administrator privilege verification & clean UAC elevation
- Single instance protection via named Windows Mutex + QLocalServer IPC
- Crash/exit safety via atexit, signal handlers, and sys.excepthook
- Rotating background logging to fan_control.log
- Seamless startup and system tray integration
"""

import sys
import os
import atexit
import ctypes
import signal
import logging
from logging.handlers import RotatingFileHandler
import win32api
import win32event
import winerror

from config import ConfigManager, get_app_dir

APP_DIR = get_app_dir()
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from hardware import LenovoWmiHardware
from main_window import MainWindow

LOG_FILE = os.path.join(APP_DIR, "fan_control.log")
log_handlers: list[logging.Handler] = [
    RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
]
if sys.stdout is not None:
    log_handlers.append(logging.StreamHandler(sys.stdout))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=log_handlers,
)
logger = logging.getLogger("Main")

IPC_SERVER_NAME = "ThinkBookFanControl_IPC_2026"
_clean_shutdown_completed = False


def emergency_fan_reset():
    """Guarantees fans are restored to factory BIOS Auto if process exits unexpectedly."""
    global _clean_shutdown_completed
    if _clean_shutdown_completed:
        return
    try:
        logger.info("Emergency exit handler: resetting fans to BIOS Auto...")
        hw = LenovoWmiHardware()
        hw.init_com()
        hw.reset_to_auto()
        hw.uninit_com()
        _clean_shutdown_completed = True
    except Exception as e:
        logger.error(f"Failed in emergency_fan_reset: {e}")


def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, initiating clean BIOS fan restore...")
    emergency_fan_reset()
    sys.exit(0)


def unhandled_exception_hook(exc_type, exc_value, exc_tb):
    logger.critical("Unhandled exception, restoring BIOS Auto!", exc_info=(exc_type, exc_value, exc_tb))
    emergency_fan_reset()
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def is_admin() -> bool:
    """Returns True if the current process has Windows Administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _run_self_test() -> int:
    """Runs non-destructive runtime smoke verification of GUI, NVML telemetry, and regulator."""
    import json
    import tempfile
    from controller import NativeSmoothRegulator
    from hardware import NvmlHardware

    app = QApplication.instance() or QApplication(sys.argv)
    nvml = NvmlHardware()
    nvml_ok = nvml.init()
    gpu_t = nvml.get_gpu_temp() if nvml_ok else None
    gpu_p = nvml.get_gpu_power() if nvml_ok else None
    nvml.shutdown()

    reg = NativeSmoothRegulator(mode="target_65", target_temp=65.0)
    step = reg.step(1.0, gpu_t or 50, 52, gpu_p or 5.0, 2150)

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        tmp_cfg = tf.name
    os.remove(tmp_cfg)
    try:
        cfg = ConfigManager(file_path=tmp_cfg)
        win = MainWindow(cfg)
        win.safe_exit()
    finally:
        if os.path.exists(tmp_cfg):
            os.remove(tmp_cfg)

    summary = {
        "self_test": "PASS",
        "nvml_ok": nvml_ok,
        "gpu_temp_c": gpu_t,
        "gpu_power_w": gpu_p,
        "regulator_cmd_rpm": step.target_rpm,
        "regulator_desc": step.mode_desc,
    }
    logger.info(f"Self-test completed: {json.dumps(summary, ensure_ascii=False)}")
    if sys.stdout is not None:
        print(json.dumps(summary, ensure_ascii=False))
    return 0


def main():
    global _clean_shutdown_completed

    if "--self-test" in sys.argv:
        os._exit(_run_self_test())

    # 0. Administrator Privileges Check & Self-Elevation
    if not is_admin():
        logger.info("Non-elevated launch detected. Requesting UAC elevation...")
        try:
            exe = sys.executable
            if getattr(sys, "frozen", False):
                params = " ".join([f'"{arg}"' for arg in sys.argv[1:]])
            else:
                params = f'"{os.path.abspath(__file__)}" ' + " ".join([f'"{arg}"' for arg in sys.argv[1:]])
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params.strip(), APP_DIR, 1)
            if ret > 32:
                os._exit(0)
            logger.error(f"UAC elevation declined or failed with code {ret}")
        except Exception as e:
            logger.error(f"Failed to elevate process: {e}")

        # Show clear Russian explanation instead of running broken without WMI access
        _tmp_app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(
            None,
            "ThinkBook Fan Control — Требуются права Администратора",
            "Для прямого управления контроллером вентиляторов Lenovo EC (root\\wmi:LENOVO_OTHER_METHOD) "
            "необходимы права Администратора.\n\nПожалуйста, подтвердите запрос UAC при запуске.",
        )
        os._exit(1)

    # 1. Single Instance Check via Windows Mutex
    mutex_name = "Local\\ThinkBookFanControlMutex_2026"
    mutex = win32event.CreateMutex(None, False, mutex_name)
    is_already_running = (win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS)

    if is_already_running:
        logger.info("Another instance is already running. Sending IPC command...")
        _ipc_app = QCoreApplication.instance() or QCoreApplication(sys.argv)
        sock = QLocalSocket()
        sock.connectToServer(IPC_SERVER_NAME)
        if sock.waitForConnected(1200):
            # Support optional CLI IPC commands (e.g., --mode target_65)
            ipc_payload = b"SHOW\n"
            if "--quit" in sys.argv:
                ipc_payload = b"QUIT\n"
            elif "--mode" in sys.argv:
                idx = sys.argv.index("--mode")
                if idx + 1 < len(sys.argv):
                    ipc_payload = f"SET_MODE:{sys.argv[idx + 1]}\n".encode("utf-8")
            sock.write(ipc_payload)
            sock.waitForBytesWritten(1000)
            sock.disconnectFromServer()
        os._exit(0)

    # 2. Primary instance registers crash & exit safety handlers
    sys.excepthook = unhandled_exception_hook
    atexit.register(emergency_fan_reset)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 3. Qt Application Setup
    app = QApplication(sys.argv)
    app.setApplicationName("ThinkBookFanControl")
    app.setOrganizationName("ThinkBookTools")
    app.setQuitOnLastWindowClosed(False)

    config = ConfigManager()
    config.ensure_autostart_migrated(is_admin=True)
    window = MainWindow(config)

    # 4. Setup IPC Server to listen for subsequent activations / mode switches
    ipc_server = QLocalServer()
    QLocalServer.removeServer(IPC_SERVER_NAME)
    ipc_server.listen(IPC_SERVER_NAME)

    def _read_ipc(sock: QLocalSocket, win: MainWindow):
        msg = sock.readAll().data().decode("utf-8", errors="ignore").strip()
        if "QUIT" in msg:
            logger.info("IPC commanded clean exit...")
            sock.disconnectFromServer()
            win.safe_exit()
            return
        if "SHOW" in msg:
            win.show_and_raise()
        elif msg.startswith("SET_MODE:"):
            mode_name = msg.split(":", 1)[1].strip()
            mode_map = {"target_65": 0, "curve": 1, "manual": 2, "auto": 3, "boost": 4}
            if mode_name in mode_map:
                win._set_mode_from_ui(mode_map[mode_name])
                logger.info(f"IPC commanded mode switch to: {mode_name}")
        sock.disconnectFromServer()

    def handle_ipc_connection():
        client = ipc_server.nextPendingConnection()
        if client:
            client.readyRead.connect(lambda c=client: _read_ipc(c, window))

    ipc_server.newConnection.connect(handle_ipc_connection)

    if config.get("start_minimized", False) or "--minimized" in sys.argv:
        window.hide()
    else:
        window.show()

    exit_code = app.exec()

    if bool(app.property("_clean_fan_shutdown")):
        _clean_shutdown_completed = True

    # Clean shutdown
    try:
        ipc_server.close()
        QLocalServer.removeServer(IPC_SERVER_NAME)
    except Exception:
        pass

    try:
        win32api.CloseHandle(mutex)
    except Exception:
        pass

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
