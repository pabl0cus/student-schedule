from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..config.models import AppConfig, ScheduleConfig, ScheduleMode
from .interval_editor import IntervalEditor


class SettingsWindow(QDialog):
    def __init__(
        self,
        config: AppConfig,
        on_save: Callable[[AppConfig], None],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Налаштування Student Schedule Worker")
        self.resize(540, 460)
        self.config_value = config
        self.on_save = on_save

        layout = QVBoxLayout(self)
        server = QLabel(f"Сервер: {config.server_url}", self)
        server.setWordWrap(True)
        layout.addWidget(server)
        layout.addWidget(QLabel("Коли дозволено виконувати транскрипцію", self))

        self.mode_group = QButtonGroup(self)
        self.mode_buttons: dict[ScheduleMode, QRadioButton] = {}
        for mode, label in (
            (ScheduleMode.ALWAYS, "Цілодобово"),
            (ScheduleMode.NEVER, "Ніколи"),
            (ScheduleMode.CUSTOM, "За інтервалами"),
        ):
            button = QRadioButton(label, self)
            button.setChecked(config.schedule.mode is mode)
            self.mode_group.addButton(button)
            self.mode_buttons[mode] = button
            layout.addWidget(button)

        layout.addWidget(QLabel("Щоденні інтервали; 22:00–06:00 переходить через північ.", self))
        self.editor = IntervalEditor(config.schedule.windows, self)
        layout.addWidget(self.editor, 1)
        self.autostart = QCheckBox("Запускати після входу у Windows", self)
        self.autostart.setChecked(config.autostart)
        layout.addWidget(self.autostart)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Зберегти")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Скасувати")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        try:
            mode = next(mode for mode, button in self.mode_buttons.items() if button.isChecked())
            updated = AppConfig(
                server_url=self.config_value.server_url,
                worker_id=self.config_value.worker_id,
                schedule=ScheduleConfig(mode=mode, windows=self.editor.get_windows()),
                autostart=self.autostart.isChecked(),
                schema_version=self.config_value.schema_version,
            )
            self.on_save(updated)
        except Exception as exc:
            QMessageBox.critical(self, "Не вдалося зберегти", str(exc))
            return
        self.accept()

