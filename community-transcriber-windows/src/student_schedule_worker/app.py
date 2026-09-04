from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QMessageBox

from .api.client import CommunityApiClient
from .auth.credential_store import CredentialStore
from .config.models import AppConfig
from .config.store import ConfigStore
from .constants import APP_NAME
from .engine.whisper import FasterWhisperEngine
from .logging_setup import configure_logging
from .platform.windows.autostart import set_autostart
from .platform.windows.paths import AppPaths
from .platform.windows.single_instance import SingleInstance
from .runtime.controller import WorkerController
from .runtime.state import STATUS_LABELS_UK, WorkerStatus
from .ui.first_run import request_first_run, request_reconnect_token
from .ui.settings_window import SettingsWindow
from .ui.tray import TrayIcon

logger = logging.getLogger(__name__)


class WorkerApplication(QObject):
    status_signal = Signal(object)

    def __init__(self, qt_app: QApplication):
        super().__init__()
        self.qt_app = qt_app
        self.qt_app.setQuitOnLastWindowClosed(False)
        self.paths = AppPaths.discover()
        self.paths.create()
        configure_logging(self.paths.logs)
        self.paths.cleanup_orphaned_jobs()
        self.config_store = ConfigStore()
        self.credentials = CredentialStore()
        self.config: AppConfig | None = None
        self.api: CommunityApiClient | None = None
        self.engine: FasterWhisperEngine | None = None
        self.controller: WorkerController | None = None
        self.tray: TrayIcon | None = None
        self.settings_window: SettingsWindow | None = None
        self._exiting = False
        self.status_signal.connect(self._apply_status)

    def _load_identity(self) -> tuple[AppConfig, str, bool] | None:
        try:
            config = self.config_store.load()
        except ValueError as exc:
            answer = QMessageBox.question(
                None,
                "Пошкоджені налаштування",
                f"{exc}\n\nСкинути локальний config.json і налаштувати підключення заново?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return None
            try:
                self.config_store.delete()
            except OSError as reset_error:
                QMessageBox.critical(None, "Не вдалося скинути налаштування", str(reset_error))
                return None
            config = None
        try:
            token = self.credentials.get(config.server_url, config.worker_id) if config is not None else None
        except Exception as exc:
            QMessageBox.critical(None, "Windows Credential Manager недоступний", str(exc))
            return None
        first_run = config is None or token is None
        if first_run:
            config = request_first_run(self.config_store, self.credentials)
            if config is None:
                return None
            try:
                token = self.credentials.get(config.server_url, config.worker_id)
            except Exception as exc:
                QMessageBox.critical(None, "Windows Credential Manager недоступний", str(exc))
                return None
        if token is None:  # pragma: no cover - guarded by the first-run dialog
            return None
        return config, token, first_run

    def _status_changed(self, status: WorkerStatus) -> None:
        self.status_signal.emit(status)

    @Slot(object)
    def _apply_status(self, status: WorkerStatus) -> None:
        label = STATUS_LABELS_UK[status.state]
        if status.detail:
            label = f"{label}: {status.detail}"
        if self.tray is not None:
            self.tray.set_status(label)

    def _save_settings(self, updated: AppConfig) -> None:
        self.config_store.save(updated)
        set_autostart(updated.autostart)
        self.config = updated
        if self.controller is not None:
            self.controller.update_config(updated)

    @Slot()
    def open_settings(self) -> None:
        if self.config is None:
            return
        if self.settings_window is not None and self.settings_window.isVisible():
            self.settings_window.raise_()
            self.settings_window.activateWindow()
            return
        self.settings_window = SettingsWindow(self.config, self._save_settings)
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    @Slot()
    def toggle_pause(self) -> None:
        if self.controller is None:
            return
        if self.controller.paused:
            self.controller.resume()
        else:
            self.controller.pause()

    def _start_controller(self, token: str) -> None:
        if self.config is None:
            return
        self.api = CommunityApiClient(self.config.server_url, token, self.config.worker_id)
        if self.engine is None:
            self.engine = FasterWhisperEngine(self.paths.models)
        self.controller = WorkerController(
            config=self.config,
            api=self.api,
            engine=self.engine,
            jobs_dir=self.paths.jobs,
            status_callback=self._status_changed,
        )
        self.controller.start()

    @Slot()
    def reconnect(self) -> None:
        if self.config is None:
            return
        if not request_reconnect_token(self.config.server_url, self.config.worker_id, self.credentials):
            return
        token = self.credentials.get(self.config.server_url, self.config.worker_id)
        if token is None:
            return
        if self.controller is not None:
            self.controller.stop()
            if self.controller.is_running:
                QMessageBox.warning(
                    None,
                    "Завершення поточної роботи",
                    "Клієнт ще завершує поточну операцію. Спробуйте оновити підключення за кілька секунд.",
                )
                return
        if self.api is not None:
            self.api.close()
        self._start_controller(token)

    @Slot()
    def disconnect(self) -> None:
        current = self.config
        if current is not None:
            answer = QMessageBox.question(
                None,
                "Змінити підключення",
                "Від’єднати цей worker, видалити його токен із Windows Credential Manager і локальні налаштування?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            if self.controller is not None:
                self.controller.stop()
                if self.controller.is_running:
                    QMessageBox.warning(
                        None,
                        "Завершення поточної роботи",
                        "Клієнт ще завершує поточну операцію. Спробуйте від’єднатися за кілька секунд.",
                    )
                    return
            try:
                self.credentials.delete(current.server_url, current.worker_id)
                self.config_store.delete()
            except Exception as exc:
                QMessageBox.critical(None, "Не вдалося від’єднати worker", str(exc))
                return
            if self.api is not None:
                self.api.close()
            self.controller = None
            self.api = None
            self.config = None
            set_autostart(False)

        replacement = request_first_run(self.config_store, self.credentials)
        if replacement is None:
            if self.tray is not None:
                self.tray.set_status("Не підключено")
            return
        try:
            token = self.credentials.get(replacement.server_url, replacement.worker_id)
        except Exception as exc:
            QMessageBox.critical(None, "Windows Credential Manager недоступний", str(exc))
            return
        if token is None:  # pragma: no cover - guarded by the onboarding dialog
            return
        self.config = replacement
        set_autostart(replacement.autostart)
        self._start_controller(token)
        QTimer.singleShot(0, self.open_settings)

    @Slot()
    def exit(self) -> None:
        if self._exiting:
            return
        self._exiting = True
        if self.controller is not None:
            self.controller.stop()
        if self.api is not None:
            self.api.close()
        if self.tray is not None:
            self.tray.stop()
        self.qt_app.quit()

    def start(self) -> bool:
        identity = self._load_identity()
        if identity is None:
            return False
        self.config, token, first_run = identity
        set_autostart(self.config.autostart)
        self.tray = TrayIcon(
            self.qt_app,
            open_settings=self.open_settings,
            reconnect=self.reconnect,
            disconnect=self.disconnect,
            toggle_pause=self.toggle_pause,
            exit_app=self.exit,
            is_paused=lambda: bool(self.controller and self.controller.paused),
        )
        self.tray.start()
        self._start_controller(token)
        if first_run:
            QTimer.singleShot(0, self.open_settings)
        return True


def run() -> int:
    qt_app = QApplication.instance() or QApplication(sys.argv)
    qt_app.setApplicationName(APP_NAME)
    instance = SingleInstance()
    if not instance.acquire():
        QMessageBox.information(None, APP_NAME, "Програма вже працює у системному треї.")
        return 0
    worker_app = WorkerApplication(qt_app)
    try:
        if not worker_app.start():
            return 1
        return qt_app.exec()
    finally:
        worker_app.exit()
        instance.release()
