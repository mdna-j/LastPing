import uuid

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from persistence.enums import ServiceStatus
from persistence.models import Service
from ui.add_service_dialog import AddServiceDialog
from ui.edit_service_dialog import EditServiceDialog


class MainWindow(QMainWindow):
    service_submitted = Signal(object)
    service_edit_submitted = Signal(object, object)
    service_pause_toggled = Signal(object, bool)

    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("LastPing")
        self.resize(1100, 700)

        self._services_by_id: dict[
            uuid.UUID,
            Service,
        ] = {}

        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self) -> None:
        # Header
        title = QLabel("LastPing")
        title.setObjectName("title")

        subtitle = QLabel(
            "Monitor the health of your services"
        )
        subtitle.setObjectName("subtitle")

        # Dashboard statistics
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(16)

        services_card, self.services_value = (
            self._create_stat_card(
                "Services"
            )
        )

        healthy_card, self.healthy_value = (
            self._create_stat_card(
                "Healthy"
            )
        )

        down_card, self.down_value = (
            self._create_stat_card(
                "Down"
            )
        )

        stats_layout.addWidget(
            services_card
        )

        stats_layout.addWidget(
            healthy_card
        )

        stats_layout.addWidget(
            down_card
        )

        # Services section header
        services_header = QHBoxLayout()

        services_label = QLabel("Services")
        services_label.setObjectName(
            "sectionTitle"
        )

        self.edit_service_button = QPushButton(
            "Edit Service"
        )
        self.edit_service_button.setObjectName(
            "secondaryButton"
        )
        self.edit_service_button.setEnabled(
            False
        )
        self.edit_service_button.clicked.connect(
            self._open_edit_service_dialog
        )

        self.pause_service_button = QPushButton(
            "Pause Service"
        )
        self.pause_service_button.setObjectName(
            "secondaryButton"
        )
        self.pause_service_button.setEnabled(
            False
        )
        self.pause_service_button.clicked.connect(
            self._toggle_selected_service
        )

        add_service_button = QPushButton(
            "+ Add Service"
        )
        add_service_button.clicked.connect(
            self._open_add_service_dialog
        )

        services_header.addWidget(
            services_label
        )

        services_header.addStretch()

        services_header.addWidget(
            self.edit_service_button
        )

        services_header.addWidget(
            self.pause_service_button
        )

        services_header.addWidget(
            add_service_button
        )

        # Services table
        self.service_table = QTableWidget()

        self.service_table.setColumnCount(
            3
        )

        self.service_table.setHorizontalHeaderLabels(
            [
                "Name",
                "Status",
                "Target",
            ]
        )

        self.service_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )

        self.service_table.verticalHeader().setVisible(
            False
        )

        self.service_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )

        self.service_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )

        self.service_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )

        self.service_table.setAlternatingRowColors(
            True
        )

        self.service_table.setRowCount(
            0
        )

        self.service_table.itemSelectionChanged.connect(
            self._update_service_actions
        )

        # Main layout
        layout = QVBoxLayout()

        layout.setContentsMargins(
            28,
            24,
            28,
            28,
        )

        layout.setSpacing(16)

        layout.addWidget(title)
        layout.addWidget(subtitle)

        layout.addSpacing(8)

        layout.addLayout(
            stats_layout
        )

        layout.addSpacing(12)

        layout.addLayout(
            services_header
        )

        layout.addWidget(
            self.service_table
        )

        container = QWidget()
        container.setLayout(layout)

        self.setCentralWidget(
            container
        )

    def _create_stat_card(
        self,
        label: str,
    ) -> tuple[QFrame, QLabel]:
        card = QFrame()
        card.setObjectName(
            "statCard"
        )

        value_label = QLabel("0")
        value_label.setObjectName(
            "statValue"
        )

        name_label = QLabel(label)
        name_label.setObjectName(
            "statLabel"
        )

        layout = QVBoxLayout()

        layout.setContentsMargins(
            18,
            14,
            18,
            14,
        )

        layout.setSpacing(4)

        layout.addWidget(
            value_label
        )

        layout.addWidget(
            name_label
        )

        card.setLayout(layout)

        return card, value_label

    def _open_add_service_dialog(
        self,
    ) -> None:
        dialog = AddServiceDialog(
            self
        )

        if (
            dialog.exec()
            == QDialog.DialogCode.Accepted
        ):
            service_data = (
                dialog.get_service_data()
            )

            self.service_submitted.emit(
                service_data
            )

    def _open_edit_service_dialog(
        self,
    ) -> None:
        service_id = (
            self._selected_service_id()
        )

        if service_id is None:
            return

        service = self._services_by_id.get(
            service_id
        )

        if service is None:
            return

        dialog = EditServiceDialog(
            service,
            self,
        )

        if (
            dialog.exec()
            == QDialog.DialogCode.Accepted
        ):
            service_data = (
                dialog.get_service_data()
            )

            self.service_edit_submitted.emit(
                service.id,
                service_data,
            )

    def _selected_service_id(
        self,
    ) -> uuid.UUID | None:
        row = self.service_table.currentRow()

        if row < 0:
            return None

        item = self.service_table.item(
            row,
            0,
        )

        if item is None:
            return None

        return item.data(
            Qt.ItemDataRole.UserRole
        )

    def _update_service_actions(
        self,
    ) -> None:
        service_id = (
            self._selected_service_id()
        )

        if service_id is None:
            self.edit_service_button.setEnabled(
                False
            )

            self.pause_service_button.setEnabled(
                False
            )

            self.pause_service_button.setText(
                "Pause Service"
            )

            return

        service = self._services_by_id.get(
            service_id
        )

        if service is None:
            self.edit_service_button.setEnabled(
                False
            )

            self.pause_service_button.setEnabled(
                False
            )

            self.pause_service_button.setText(
                "Pause Service"
            )

            return

        self.edit_service_button.setEnabled(
            True
        )

        self.pause_service_button.setEnabled(
            True
        )

        if service.is_paused:
            self.pause_service_button.setText(
                "Resume Service"
            )
        else:
            self.pause_service_button.setText(
                "Pause Service"
            )

    def _toggle_selected_service(
        self,
    ) -> None:
        service_id = (
            self._selected_service_id()
        )

        if service_id is None:
            return

        service = self._services_by_id.get(
            service_id
        )

        if service is None:
            return

        should_pause = (
            not service.is_paused
        )

        self.service_pause_toggled.emit(
            service.id,
            should_pause,
        )

    def set_services(
        self,
        services: list[Service],
    ) -> None:
        selected_service_id = (
            self._selected_service_id()
        )

        self._services_by_id = {
            service.id: service
            for service in services
        }

        self.service_table.setRowCount(
            len(services)
        )

        healthy_count = 0
        down_count = 0

        selected_row = None

        status_colors = {
            ServiceStatus.HEALTHY: QColor(
                "#4ade80"
            ),
            ServiceStatus.DOWN: QColor(
                "#f87171"
            ),
            ServiceStatus.DEGRADED: QColor(
                "#fbbf24"
            ),
            ServiceStatus.PAUSED: QColor(
                "#9ca3af"
            ),
            ServiceStatus.UNKNOWN: QColor(
                "#9ca3af"
            ),
        }

        for row, service in enumerate(
            services
        ):
            status = (
                service.current_status.value.title()
            )

            # Name
            name_item = QTableWidgetItem(
                service.name
            )

            name_item.setData(
                Qt.ItemDataRole.UserRole,
                service.id,
            )

            self.service_table.setItem(
                row,
                0,
                name_item,
            )

            # Status
            status_item = QTableWidgetItem(
                f"● {status}"
            )

            status_item.setForeground(
                status_colors.get(
                    service.current_status,
                    QColor("#f2f2f2"),
                )
            )

            self.service_table.setItem(
                row,
                1,
                status_item,
            )

            # Target
            self.service_table.setItem(
                row,
                2,
                QTableWidgetItem(
                    service.target
                ),
            )

            # Statistics
            if (
                service.current_status
                == ServiceStatus.HEALTHY
            ):
                healthy_count += 1

            if (
                service.current_status
                == ServiceStatus.DOWN
            ):
                down_count += 1

            # Restore selected row
            if (
                service.id
                == selected_service_id
            ):
                selected_row = row

        self.services_value.setText(
            str(len(services))
        )

        self.healthy_value.setText(
            str(healthy_count)
        )

        self.down_value.setText(
            str(down_count)
        )

        if selected_row is not None:
            self.service_table.selectRow(
                selected_row
            )

        self._update_service_actions()

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background-color: #181a1f;
            }

            QLabel {
                color: #f2f2f2;
            }

            QLabel#title {
                font-size: 28px;
                font-weight: 700;
            }

            QLabel#subtitle {
                color: #9ca3af;
                font-size: 13px;
            }

            QLabel#sectionTitle {
                font-size: 18px;
                font-weight: 600;
            }

            QFrame#statCard {
                background-color: #23262d;
                border: 1px solid #343840;
                border-radius: 8px;
            }

            QLabel#statValue {
                font-size: 24px;
                font-weight: 700;
            }

            QLabel#statLabel {
                color: #9ca3af;
                font-size: 12px;
            }

            QPushButton {
                background-color: #2563eb;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 14px;
                font-weight: 600;
            }

            QPushButton:hover {
                background-color: #3b82f6;
            }

            QPushButton:pressed {
                background-color: #1d4ed8;
            }

            QPushButton:disabled {
                background-color: #343840;
                color: #737b88;
            }

            QPushButton#secondaryButton {
                background-color: #343840;
                color: #f2f2f2;
            }

            QPushButton#secondaryButton:hover {
                background-color: #454b55;
            }

            QPushButton#secondaryButton:pressed {
                background-color: #2b3037;
            }

            QPushButton#secondaryButton:disabled {
                background-color: #2a2e35;
                color: #666d78;
            }

            QTableWidget {
                background-color: #202329;
                alternate-background-color: #24272e;
                color: #f2f2f2;
                border: 1px solid #343840;
                border-radius: 6px;
                gridline-color: #343840;
                selection-background-color: #3a4456;
                selection-color: white;
            }

            QHeaderView::section {
                background-color: #2a2e35;
                color: #d1d5db;
                border: none;
                border-bottom: 1px solid #3b4048;
                padding: 8px;
                font-weight: 600;
            }

            QTableWidget::item {
                padding: 6px;
            }
            """
        )