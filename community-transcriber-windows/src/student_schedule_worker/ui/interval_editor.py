from __future__ import annotations

from PySide6.QtCore import QTime
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ..config.models import TimeWindow


class IntervalEditor(QWidget):
    def __init__(self, windows: tuple[TimeWindow, ...] = (), parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["Початок", "Кінець"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        controls = QHBoxLayout()
        self.start_edit = QTimeEdit(QTime(10, 0), self)
        self.end_edit = QTimeEdit(QTime(16, 0), self)
        self.start_edit.setDisplayFormat("HH:mm")
        self.end_edit.setDisplayFormat("HH:mm")
        add_button = QPushButton("Додати", self)
        remove_button = QPushButton("Видалити", self)
        add_button.clicked.connect(self._add)
        remove_button.clicked.connect(self._remove)
        controls.addWidget(self.start_edit)
        controls.addWidget(self.end_edit)
        controls.addWidget(add_button)
        controls.addWidget(remove_button)
        controls.addStretch(1)
        layout.addLayout(controls)

        for window in windows:
            self._append(window)

    def _append(self, window: TimeWindow) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(window.start))
        self.table.setItem(row, 1, QTableWidgetItem(window.end))

    def _add(self) -> None:
        start = self.start_edit.time().toString("HH:mm")
        end = self.end_edit.time().toString("HH:mm")
        try:
            window = TimeWindow(start, end)
        except ValueError:
            QMessageBox.warning(self, "Некоректний інтервал", "Початок і кінець не можуть бути однаковими.")
            return
        self._append(window)

    def _remove(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)

    def get_windows(self) -> tuple[TimeWindow, ...]:
        return tuple(
            TimeWindow(self.table.item(row, 0).text(), self.table.item(row, 1).text())
            for row in range(self.table.rowCount())
        )
