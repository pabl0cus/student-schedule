from __future__ import annotations

from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ..api.client import CommunityApiClient
from ..api.security import normalized_server_origin
from ..auth.credential_store import CredentialStore
from ..config.models import AppConfig
from ..config.store import ConfigStore


class FirstRunDialog(QDialog):
    def __init__(self, config_store: ConfigStore, credentials: CredentialStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Підключення Student Schedule Worker")
        self.setMinimumWidth(500)
        self.config_store = config_store
        self.credentials = credentials
        self.result_config: AppConfig | None = None

        layout = QVBoxLayout(self)
        description = QLabel(
            "Цей комп’ютер допомагатиме транскрибувати лекції у фоні. Після підключення оберіть дозволені години.",
            self,
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        form = QFormLayout()
        self.server_edit = QLineEdit(self)
        self.server_edit.setPlaceholderText("https://your-schedule.example")
        self.token_edit = QLineEdit(self)
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Адреса сервера", self.server_edit)
        form.addRow("Токен від адміністратора", self.token_edit)
        layout.addLayout(form)
        hint = QLabel("HTTP дозволений лише для localhost. Для публічного сервера потрібен HTTPS.", self)
        hint.setWordWrap(True)
        hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Підключити")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Закрити")
        buttons.accepted.connect(self._connect)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _connect(self) -> None:
        token = self.token_edit.text().strip()
        try:
            server_url = normalized_server_origin(self.server_edit.text())
            worker_id = str(uuid4())
            config = AppConfig(server_url=server_url, worker_id=worker_id)
            with CommunityApiClient(server_url, token, worker_id) as api:
                api.get_me()
            self.credentials.set(server_url, worker_id, token)
            try:
                self.config_store.save(config)
            except Exception:
                self.credentials.delete(server_url, worker_id)
                raise
        except Exception as exc:
            QMessageBox.critical(self, "Не вдалося підключитися", str(exc))
            return
        self.token_edit.clear()
        self.result_config = config
        self.accept()


def request_first_run(
    config_store: ConfigStore,
    credentials: CredentialStore,
    parent: QWidget | None = None,
) -> AppConfig | None:
    dialog = FirstRunDialog(config_store, credentials, parent)
    return dialog.result_config if dialog.exec() == QDialog.DialogCode.Accepted else None


class ReconnectDialog(QDialog):
    def __init__(
        self,
        server_url: str,
        worker_id: str,
        credentials: CredentialStore,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Оновити токен")
        self.setMinimumWidth(460)
        self.server_url = server_url
        self.worker_id = worker_id
        self.credentials = credentials
        self.accepted_token = False
        layout = QVBoxLayout(self)
        server = QLabel(f"Сервер: {server_url}", self)
        server.setWordWrap(True)
        layout.addWidget(server)
        self.token_edit = QLineEdit(self)
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setPlaceholderText("Новий токен від адміністратора")
        layout.addWidget(self.token_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Перевірити й зберегти")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        token = self.token_edit.text().strip()
        try:
            with CommunityApiClient(self.server_url, token, self.worker_id) as api:
                api.get_me()
            self.credentials.set(self.server_url, self.worker_id, token)
        except Exception as exc:
            QMessageBox.critical(self, "Токен не прийнято", str(exc))
            return
        self.token_edit.clear()
        self.accepted_token = True
        self.accept()


def request_reconnect_token(
    server_url: str,
    worker_id: str,
    credentials: CredentialStore,
    parent: QWidget | None = None,
) -> bool:
    dialog = ReconnectDialog(server_url, worker_id, credentials, parent)
    return dialog.exec() == QDialog.DialogCode.Accepted and dialog.accepted_token
