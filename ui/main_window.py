from PySide6.QtWidgets import (
    QAbstractItemView,
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
        self.resize(1000, 650)

        self._setup_ui()

    def _setup_ui(self) -> None:
        title = QLabel("LastPing")

        services_label = QLabel("Services")

        service_table = QTableWidget()
        service_table.setColumnCount(3)
        service_table.setHorizontalHeaderLabels(
            ["Name", "Status", "Target"]
        )

        service_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )

        service_table.verticalHeader().setVisible(False)

        service_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )

        service_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )

        service_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )

        service_table.setAlternatingRowColors(True)

        # Temporary sample data
        sample_services = [
            ("Example Website", "Healthy", "https://example.com"),
            ("GitHub", "Healthy", "https://github.com"),
            ("Local Server", "Down", "http://localhost:8000"),
        ]

        service_table.setRowCount(len(sample_services))

        for row, service in enumerate(sample_services):
            name, status, target = service

            service_table.setItem(
                row,
                0,
                QTableWidgetItem(name),
            )

            service_table.setItem(
                row,
                1,
                QTableWidgetItem(status),
            )

            service_table.setItem(
                row,
                2,
                QTableWidgetItem(target),
            )

        layout = QVBoxLayout()
        layout.addWidget(title)
        layout.addWidget(services_label)
        layout.addWidget(service_table)

        container = QWidget()
        container.setLayout(layout)

        self.setCentralWidget(container)