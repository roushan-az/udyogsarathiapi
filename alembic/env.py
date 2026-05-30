import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import pool

# ... (keep your existing target_metadata setup here) ...

def do_run_migrations(connection):
    """A helper function to run the actual migrations synchronously within the async loop."""
    context.configure(
        connection=connection, 
        target_metadata=target_metadata
    )
    with context.begin_transaction():
        context.run_migrations()

async def run_async_migrations():
    """Create an async engine and run migrations using run_sync."""
    # This safely uses your exact async DATABASE_URL from your .env file
    connectable = create_async_engine(
        str(settings.DATABASE_URL),
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        # This is the magic line: it bridges the async driver with Alembic's sync code
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()

def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # Spin up an async event loop specifically for Alembic
    asyncio.run(run_async_migrations())