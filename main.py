"""LastPing application entry point."""

import asyncio
import sys

from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from engine.scheduler import MonitoringScheduler
from persistence.database import AsyncSessionLocal, close_database
from persistence.repositories.service_repository import ServiceRepository
from service_layer.application_service import ApplicationService
from ui.main_window import MainWindow


async def bootstrap() -> tuple[MainWindow, ApplicationService]:
    scheduler = MonitoringScheduler()

    async with AsyncSessionLocal() as session:
        service_repository = ServiceRepository(session)

        application = ApplicationService(
            service_repository=service_repository,
            scheduler=scheduler,
        )

        # Start monitoring all saved active services.
        await application.start()

        # Load those same services for the dashboard.
        services = await service_repository.list_active()

    window = MainWindow()
    window.set_services(services)
    window.show()

    return window, application


async def refresh_dashboard(window: MainWindow) -> None:
    while True:
        await asyncio.sleep(5)

        async with AsyncSessionLocal() as session:
            service_repository = ServiceRepository(session)
            services = await service_repository.list_active()

        window.set_services(services)


def main() -> None:
    app = QApplication(sys.argv)

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    application = None
    refresh_task = None

    with loop:
        try:
            window, application = loop.run_until_complete(
                bootstrap()
            )

            refresh_task = loop.create_task(
                refresh_dashboard(window)
            )

            loop.run_forever()

        finally:
            if refresh_task is not None:
                refresh_task.cancel()

                try:
                    loop.run_until_complete(refresh_task)
                except asyncio.CancelledError:
                    pass

            if application is not None:
                application.shutdown()

            loop.run_until_complete(close_database())


if __name__ == "__main__":
    main()