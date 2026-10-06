from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("LastPing")
        self.resize(1000, 650)

        title = QLabel("LastPing")

        layout = QVBoxLayout()
        layout.addWidget(title)

        container = QWidget()
        container.setLayout(layout)

        self.setCentralWidget(container)