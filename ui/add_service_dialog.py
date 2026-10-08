from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)

from persistence.enums import MonitorType


class AddServiceDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.setWindowTitle("Add Service")
        self.setMinimumWidth(420)

        self._setup_ui()

    def _setup_ui(self) -> None:
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("My Website")

        self.type_input = QComboBox()
        self.type_input.addItem("HTTP", MonitorType.HTTP)
        self.type_input.addItem("HTTPS", MonitorType.HTTPS)

        self.target_input = QLineEdit()
        self.target_input.setPlaceholderText("https://example.com")

        self.interval_input = QSpinBox()
        self.interval_input.setRange(10, 86400)
        self.interval_input.setValue(60)
        self.interval_input.setSuffix(" seconds")

        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(1, 300)
        self.timeout_input.setValue(10)
        self.timeout_input.setSuffix(" seconds")

        form_layout = QFormLayout()
        form_layout.addRow("Name", self.name_input)
        form_layout.addRow("Monitor Type", self.type_input)
        form_layout.addRow("Target", self.target_input)
        form_layout.addRow("Check Interval", self.interval_input)
        form_layout.addRow("Timeout", self.timeout_input)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok
        )

        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form_layout)
        layout.addWidget(buttons)

        self.setLayout(layout)