from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from persistence.enums import ServiceStatus
from persistence.models import Service


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("LastPing")
        self.resize(1100, 700)

        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self) -> None:
        # Header
        title = QLabel("LastPing")
        title.setObjectName("title")

        subtitle = QLabel("Monitor the health of your services")
        subtitle.setObjectName("subtitle")

        # Dashboard statistics
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(16)

        services_card, self.services_value = self._create_stat_card(
            "Services"
        )
        healthy_card, self.healthy_value = self._create_stat_card(
            "Healthy"
        )
        down_card, self.down_value = self._create_stat_card(
            "Down"
        )

        stats_layout.addWidget(services_card)
        stats_layout.addWidget(healthy_card)
        stats_layout.addWidget(down_card)

        # Services section
        services_label = QLabel("Services")
        services_label.setObjectName("sectionTitle")

        self.service_table = QTableWidget()
        self.service_table.setColumnCount(3)
        self.service_table.setHorizontalHeaderLabels(
            ["Name", "Status", "Target"]
        )

        self.service_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )

        self.service_table.verticalHeader().setVisible(False)

        self.service_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )

        self.service_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )

        self.service_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )

        self.service_table.setAlternatingRowColors(True)
        self.service_table.setRowCount(0)

        # Main layout
        layout = QVBoxLayout()
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(16)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(8)
        layout.addLayout(stats_layout)
        layout.addSpacing(12)
        layout.addWidget(services_label)
        layout.addWidget(self.service_table)

        container = QWidget()
        container.setLayout(layout)

        self.setCentralWidget(container)

    def _create_stat_card(
        self,
        label: str,
    ) -> tuple[QFrame, QLabel]:
        card = QFrame()
        card.setObjectName("statCard")

        value_label = QLabel("0")
        value_label.setObjectName("statValue")

        name_label = QLabel(label)
        name_label.setObjectName("statLabel")

        layout = QVBoxLayout()
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(4)

        layout.addWidget(value_label)
        layout.addWidget(name_label)

        card.setLayout(layout)

        return card, value_label

    def set_services(self, services: list[Service]) -> None:
        self.service_table.setRowCount(len(services))

        healthy_count = 0
        down_count = 0

        status_colors = {
            ServiceStatus.HEALTHY: QColor("#4ade80"),
            ServiceStatus.DOWN: QColor("#f87171"),
            ServiceStatus.DEGRADED: QColor("#fbbf24"),
            ServiceStatus.PAUSED: QColor("#9ca3af"),
            ServiceStatus.UNKNOWN: QColor("#9ca3af"),
        }

        for row, service in enumerate(services):
            status = service.current_status.value.title()

            self.service_table.setItem(
                row,
                0,
                QTableWidgetItem(service.name),
            )

            status_item = QTableWidgetItem(f"● {status}")

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

            self.service_table.setItem(
                row,
                2,
                QTableWidgetItem(service.target),
            )

            if service.current_status == ServiceStatus.HEALTHY:
                healthy_count += 1

            if service.current_status == ServiceStatus.DOWN:
                down_count += 1

        self.services_value.setText(str(len(services)))
        self.healthy_value.setText(str(healthy_count))
        self.down_value.setText(str(down_count))

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