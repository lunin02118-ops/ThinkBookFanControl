"""
Application Entry Point for Lenovo ThinkBook Fan Control
Features:
- Single instance protection via named Windows Mutex
- Crash/exit safety via atexit and signal handlers
- Background logging to fan_control.log
- Seamless startup and system tray integration
"""

import sys
import os
import atexit
import signal
import logging
import win32api
import win32event
import winerror

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt

# Ensure local imports work regardless of CWD
from config import ConfigManager, get_app_dir
APP_DIR = get_app_dir()
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from PySide6.QtNetwork import QLocalServer, QLocalSocket
from hardware import LenovoWmiHardware
from main_window import MainWindow

LOG_FILE = os.path.join(APP_DIR, "fan_control.log")
log_handlers = [logging.FileHandler(LOG_FILE, encoding="utf-8")]
if sys.stdout is not None:
    log_handlers.append(logging.StreamHandler(sys.stdout))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=log_handlers,
)
logger = logging.getLogger("Main")


def emergency_fan_reset():
    """Guarantee fans are restored to factory BIOS default if process is terminated."""
    try:
        logger.info("Emergency exit handler: resetting fans to BIOS Auto...")
        hw = LenovoWmiHardware()
        hw.init_com()
        hw.reset_to_auto()
        hw.uninit_com()
    except Exception as e:
        logger.error(f"Failed in emergency_fan_reset: {e}")


def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, initiating clean exit...")
    emergency_fan_reset()
    sys.exit(0)


IPC_SERVER_NAME = "ThinkBookFanControl_IPC_2026"


def main():
    # 1. Single Instance Check via Windows Mutex
    mutex_name = "Local\\ThinkBookFanControlMutex_2026"
    mutex = win32event.CreateMutex(None, False, mutex_name)
    is_already_running = (win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS)

    if is_already_running:
        # Send wake/show message to the active running instance
        logger.info("Another instance is already running. Sending show signal via IPC...")
        sock = QLocalSocket()
        sock.connectToServer(IPC_SERVER_NAME)
        if sock.waitForConnected(1200):
            sock.write(b"SHOW\n")
            sock.waitForBytesWritten(1000)
            sock.disconnectFromServer()
        # Use os._exit to avoid running atexit emergency fan reset on secondary launcher!
        os._exit(0)

    # 2. Only the primary instance registers exit safety handlers
    atexit.register(emergency_fan_reset)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 2. Qt Application Setup
    app = QApplication(sys.argv)
    app.setApplicationName("ThinkBookFanControl")
    app.setOrganizationName("LenovoMinerTools")
    app.setQuitOnLastWindowClosed(False)  # keep running in system tray

    config = ConfigManager()
    window = MainWindow(config)

    # 3. Setup IPC Server to listen for subsequent activations
    ipc_server = QLocalServer()
    QLocalServer.removeServer(IPC_SERVER_NAME)
    ipc_server.listen(IPC_SERVER_NAME)

    def handle_ipc_connection():
        client = ipc_server.nextPendingConnection()
        if client:
            client.readyRead.connect(lambda: _read_ipc(client, window))

    def _read_ipc(sock, win):
        msg = sock.readAll().data().decode("utf-8", errors="ignore").strip()
        if "SHOW" in msg:
            win.show_and_raise()
        elif msg.startswith("SET_MODE:"):
            mode_name = msg.split(":", 1)[1].strip()
            mode_map = {"target_65": 0, "curve": 1, "manual": 2, "auto": 3, "boost": 4}
            if mode_name in mode_map:
                win._set_mode_from_ui(mode_map[mode_name])
                logger.info(f"IPC commanded mode switch to: {mode_name}")
        sock.disconnectFromServer()

    ipc_server.newConnection.connect(handle_ipc_connection)

    if config.get("start_minimized", False):
        window.hide()
    else:
        window.show()

    exit_code = app.exec()

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
