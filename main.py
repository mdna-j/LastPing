"""LastPing application entry point."""

import asyncio
import sys
import uuid

from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from engine.scheduler import MonitoringScheduler
from persistence.database import (
    AsyncSessionLocal,
    close_database,
)
from persistence.enums import ServiceStatus
from persistence.models import Service
from persistence.repositories.service_repository import (
    ServiceRepository,
)
from service_layer.application_service import (
    ApplicationService,
)
from ui.main_window import MainWindow


async def bootstrap(
) -> tuple[
    MainWindow,
    ApplicationService,
    MonitoringScheduler,
]:
    scheduler = MonitoringScheduler()

    async with AsyncSessionLocal() as session:
        service_repository = ServiceRepository(
            session
        )

        application = ApplicationService(
            service_repository=service_repository,
            scheduler=scheduler,
        )

        # Start monitoring all saved active services.
        await application.start()

        # Load services for the dashboard.
        services = (
            await service_repository.list_active()
        )

    window = MainWindow()

    window.set_services(
        services
    )

    window.show()

    return (
        window,
        application,
        scheduler,
    )


async def handle_add_service(
    service_data: dict,
    scheduler: MonitoringScheduler,
    window: MainWindow,
) -> None:
    service = Service(
        **service_data
    )

    async with AsyncSessionLocal() as session:
        service_repository = ServiceRepository(
            session
        )

        saved_service = (
            await service_repository.create(
                service
            )
        )

        # Start monitoring immediately.
        scheduler.schedule_service(
            saved_service
        )

        services = (
            await service_repository.list_active()
        )

    window.set_services(
        services
    )


async def handle_edit_service(
    service_id: uuid.UUID,
    service_data: dict,
    scheduler: MonitoringScheduler,
    window: MainWindow,
) -> None:
    async with AsyncSessionLocal() as session:
        service_repository = ServiceRepository(
            session
        )

        service = (
            await service_repository.get_by_id(
                service_id
            )
        )

        if service is None:
            return

        service.name = service_data[
            "name"
        ]

        service.type = service_data[
            "type"
        ]

        service.target = service_data[
            "target"
        ]

        service.interval_seconds = service_data[
            "interval_seconds"
        ]

        service.timeout_seconds = service_data[
            "timeout_seconds"
        ]

        # Editing a service should trigger
        # a fresh health evaluation.
        service.consecutive_failures = 0

        if service.is_paused:
            service.current_status = (
                ServiceStatus.PAUSED
            )
        else:
            service.current_status = (
                ServiceStatus.UNKNOWN
            )

        saved_service = (
            await service_repository.update(
                service
            )
        )

        if saved_service.is_paused:
            scheduler.remove_service(
                saved_service.id
            )
        else:
            # replace_existing=True inside
            # the scheduler updates the job
            # with the new interval/settings.
            scheduler.schedule_service(
                saved_service
            )

        services = (
            await service_repository.list_active()
        )

    window.set_services(
        services
    )


async def handle_service_pause_toggle(
    service_id: uuid.UUID,
    should_pause: bool,
    scheduler: MonitoringScheduler,
    window: MainWindow,
) -> None:
    async with AsyncSessionLocal() as session:
        service_repository = ServiceRepository(
            session
        )

        service = (
            await service_repository.get_by_id(
                service_id
            )
        )

        if service is None:
            return

        service.is_paused = should_pause

        if should_pause:
            service.current_status = (
                ServiceStatus.PAUSED
            )
        else:
            service.current_status = (
                ServiceStatus.UNKNOWN
            )

        saved_service = (
            await service_repository.update(
                service
            )
        )

        if should_pause:
            scheduler.remove_service(
                saved_service.id
            )
        else:
            scheduler.schedule_service(
                saved_service
            )

        services = (
            await service_repository.list_active()
        )

    window.set_services(
        services
    )


async def refresh_dashboard(
    window: MainWindow,
) -> None:
    while True:
        await asyncio.sleep(5)

        async with AsyncSessionLocal() as session:
            service_repository = ServiceRepository(
                session
            )

            services = (
                await service_repository.list_active()
            )

        window.set_services(
            services
        )


def main() -> None:
    app = QApplication(
        sys.argv
    )

    loop = QEventLoop(app)

    asyncio.set_event_loop(
        loop
    )

    application = None
    refresh_task = None

    with loop:
        try:
            (
                window,
                application,
                scheduler,
            ) = loop.run_until_complete(
                bootstrap()
            )

            def on_service_submitted(
                service_data: dict,
            ) -> None:
                loop.create_task(
                    handle_add_service(
                        service_data,
                        scheduler,
                        window,
                    )
                )

            def on_service_edit_submitted(
                service_id: uuid.UUID,
                service_data: dict,
            ) -> None:
                loop.create_task(
                    handle_edit_service(
                        service_id,
                        service_data,
                        scheduler,
                        window,
                    )
                )

            def on_service_pause_toggled(
                service_id: uuid.UUID,
                should_pause: bool,
            ) -> None:
                loop.create_task(
                    handle_service_pause_toggle(
                        service_id,
                        should_pause,
                        scheduler,
                        window,
                    )
                )

            window.service_submitted.connect(
                on_service_submitted
            )

            window.service_edit_submitted.connect(
                on_service_edit_submitted
            )

            window.service_pause_toggled.connect(
                on_service_pause_toggled
            )

            refresh_task = loop.create_task(
                refresh_dashboard(
                    window
                )
            )

            loop.run_forever()

        finally:
            if refresh_task is not None:
                refresh_task.cancel()

                try:
                    loop.run_until_complete(
                        refresh_task
                    )
                except asyncio.CancelledError:
                    pass

            if application is not None:
                application.shutdown()

            loop.run_until_complete(
                close_database()
            )


if __name__ == "__main__":
    main()