from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
)

from persistence.enums import MonitorType
from persistence.models import Service


class EditServiceDialog(QDialog):
    def __init__(
        self,
        service: Service,
        parent=None,
    ) -> None:
        super().__init__(parent)

        self.service = service

        self.setWindowTitle("Edit Service")
        self.setMinimumWidth(420)

        self._setup_ui()

    def _setup_ui(self) -> None:
        self.name_input = QLineEdit()
        self.name_input.setText(self.service.name)

        self.type_input = QComboBox()
        self.type_input.addItem(
            "HTTP",
            MonitorType.HTTP,
        )
        self.type_input.addItem(
            "HTTPS",
            MonitorType.HTTPS,
        )

        current_type_index = self.type_input.findData(
            self.service.type
        )

        if current_type_index >= 0:
            self.type_input.setCurrentIndex(
                current_type_index
            )

        self.target_input = QLineEdit()
        self.target_input.setText(
            self.service.target
        )

        self.interval_input = QSpinBox()
        self.interval_input.setRange(
            10,
            86400,
        )
        self.interval_input.setValue(
            self.service.interval_seconds
        )
        self.interval_input.setSuffix(
            " seconds"
        )

        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(
            1,
            300,
        )
        self.timeout_input.setValue(
            self.service.timeout_seconds
        )
        self.timeout_input.setSuffix(
            " seconds"
        )

        form_layout = QFormLayout()

        form_layout.addRow(
            "Name",
            self.name_input,
        )

        form_layout.addRow(
            "Monitor Type",
            self.type_input,
        )

        form_layout.addRow(
            "Target",
            self.target_input,
        )

        form_layout.addRow(
            "Check Interval",
            self.interval_input,
        )

        form_layout.addRow(
            "Timeout",
            self.timeout_input,
        )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Save
        )

        buttons.accepted.connect(
            self.accept
        )

        buttons.rejected.connect(
            self.reject
        )

        layout = QVBoxLayout()

        layout.addLayout(
            form_layout
        )

        layout.addWidget(
            buttons
        )

        self.setLayout(layout)

    def get_service_data(self) -> dict:
        return {
            "name": self.name_input.text().strip(),
            "type": self.type_input.currentData(),
            "target": self.target_input.text().strip(),
            "interval_seconds": self.interval_input.value(),
            "timeout_seconds": self.timeout_input.value(),
        }

    def accept(self) -> None:
        if not self.name_input.text().strip():
            QMessageBox.warning(
                self,
                "Invalid Service",
                "Service name is required.",
            )
            return

        if not self.target_input.text().strip():
            QMessageBox.warning(
                self,
                "Invalid Service",
                "Target is required.",
            )
            return

        super().accept()