# Database engine and session configuration for LastPing

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config.settings import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url, echo=False)

AsyncSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False
)


async def get_async_session():
    # Provide a database session
    async with AsyncSessionLocal() as session:
        yield session


async def close_database():
    # Dispose of database engine
    await engine.dispose()
