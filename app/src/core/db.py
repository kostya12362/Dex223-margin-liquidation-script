from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from config import settings
from models import Base

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=20,  # choose one for your load
    max_overflow=20,
    pool_pre_ping=True,
)

# Session Factory: Open a new session for each parallel operation
SessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db():
    # create tables if not exist (optional if using alembic)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
