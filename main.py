"""LastPing application entry point."""

import asyncio

from engine.scheduler import MonitoringScheduler
from persistence.database import AsyncSessionLocal, close_database
from persistence.repositories.service_repository import ServiceRepository
from service_layer.application_service import ApplicationService


async def run() -> None:
    """Start LastPing and keep background monitoring running."""

    scheduler = MonitoringScheduler()

    async with AsyncSessionLocal() as session:
        service_repository = ServiceRepository(session)

        application = ApplicationService(
            service_repository=service_repository,
            scheduler=scheduler,
        )

        await application.start()

    print("LastPing started.")
    print("Monitoring active services.")
    print("Press Ctrl+C to stop.")

    try:
        await asyncio.Event().wait()
    finally:
        application.shutdown()
        await close_database()


def main() -> None:
    """Run the LastPing application."""

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nLastPing stopped.")


if __name__ == "__main__":
    main()
