from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ..constants import APP_NAME


def _icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(26, 69, 133))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 60, 60, 12, 12)
    painter.setBrush(QColor(255, 255, 255))
    painter.drawRoundedRect(14, 11, 36, 43, 5, 5)
    painter.setBrush(QColor(26, 69, 133))
    for y, width in ((21, 23), (31, 20), (41, 16)):
        painter.drawRoundedRect(20, y, width, 4, 2, 2)
    painter.end()
    return QIcon(pixmap)


class TrayIcon:
    def __init__(
        self,
        app: QApplication,
        *,
        open_settings: Callable[[], None],
        reconnect: Callable[[], None],
        disconnect: Callable[[], None],
        toggle_pause: Callable[[], None],
        exit_app: Callable[[], None],
        is_paused: Callable[[], bool],
    ):
        self._is_paused = is_paused
        self.icon = QSystemTrayIcon(_icon(), app)
        self.icon.setToolTip(APP_NAME)
        self.menu = QMenu()
        self.status_action = QAction("Запуск", self.menu)
        self.status_action.setEnabled(False)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()
        self.settings_action = self.menu.addAction("Налаштування")
        self.settings_action.triggered.connect(open_settings)
        self.reconnect_action = self.menu.addAction("Оновити токен")
        self.reconnect_action.triggered.connect(reconnect)
        self.disconnect_action = self.menu.addAction("Змінити сервер / від’єднати")
        self.disconnect_action.triggered.connect(disconnect)
        self.pause_action = self.menu.addAction("Призупинити")
        self.pause_action.triggered.connect(toggle_pause)
        self.exit_action = self.menu.addAction("Вихід")
        self.exit_action.triggered.connect(exit_app)
        self.menu.aboutToShow.connect(self._refresh_pause_label)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(
            lambda reason: open_settings() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None
        )

    def _refresh_pause_label(self) -> None:
        self.pause_action.setText("Продовжити" if self._is_paused() else "Призупинити")

    def start(self) -> None:
        self.icon.show()

    def set_status(self, status: str) -> None:
        self.status_action.setText(status)
        self.icon.setToolTip(f"{APP_NAME}: {status}")

    def stop(self) -> None:
        self.icon.hide()
