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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("LastPing")
        self.resize(1100, 700)

        self._setup_ui()
        self._apply_styles()

    def _setup_ui(self) -> None:
        # Temporary sample data
        sample_services = [
            ("Example Website", "Healthy", "https://example.com"),
            ("GitHub", "Healthy", "https://github.com"),
            ("Local Server", "Down", "http://localhost:8000"),
        ]

        # Header
        title = QLabel("LastPing")
        title.setObjectName("title")

        subtitle = QLabel("Monitor the health of your services")
        subtitle.setObjectName("subtitle")

        # Dashboard statistics
        total_services = len(sample_services)
        healthy_services = sum(
            1 for service in sample_services if service[1] == "Healthy"
        )
        down_services = sum(
            1 for service in sample_services if service[1] == "Down"
        )

        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(16)

        stats_layout.addWidget(
            self._create_stat_card("Services", str(total_services))
        )
        stats_layout.addWidget(
            self._create_stat_card("Healthy", str(healthy_services))
        )
        stats_layout.addWidget(
            self._create_stat_card("Down", str(down_services))
        )

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

        self.service_table.setRowCount(len(sample_services))

        for row, service in enumerate(sample_services):
            name, status, target = service

            self.service_table.setItem(
                row,
                0,
                QTableWidgetItem(name),
            )

            self.service_table.setItem(
                row,
                1,
                QTableWidgetItem(status),
            )

            self.service_table.setItem(
                row,
                2,
                QTableWidgetItem(target),
            )

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

    def _create_stat_card(self, label: str, value: str) -> QFrame:
        card = QFrame()
        card.setObjectName("statCard")

        value_label = QLabel(value)
        value_label.setObjectName("statValue")

        name_label = QLabel(label)
        name_label.setObjectName("statLabel")

        layout = QVBoxLayout()
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(4)

        layout.addWidget(value_label)
        layout.addWidget(name_label)

        card.setLayout(layout)

        return card

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