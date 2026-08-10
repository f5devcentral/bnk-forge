"""
Database connection and session management for BNK-Forge.
Supports SQLite for MVP with easy migration to PostgreSQL.
"""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# Database configuration — sourced from Pydantic Settings (core.config)
# This ensures DATABASE_URL is validated and consistent with .env loading
from core.config import settings

DATABASE_URL = settings.DATABASE_URL

# Create engine with connection pooling
# PERFORMANCE OPTIMIZATION (ADR-019):
# Added explicit pool configuration to handle concurrent requests better
# For SQLite: check_same_thread=False allows usage across threads
# For PostgreSQL: uses connection pooling with proper settings
if DATABASE_URL.startswith("sqlite"):
    # SQLite doesn't support connection pooling the same way
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        echo=False,
    )
else:
    # PostgreSQL with connection pooling
    engine = create_engine(
        DATABASE_URL,
        pool_size=10,           # Base number of connections to keep
        max_overflow=20,        # Allow up to 20 additional connections under load
        pool_recycle=3600,      # Recycle connections after 1 hour (prevents stale connections)
        pool_pre_ping=True,     # Test connections before use (handles dropped connections)
        pool_timeout=30,        # Timeout for getting connection from pool
        echo=False,             # Set to True for SQL query logging during development
    )

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create base class for declarative models (SQLAlchemy 2.0+ style)
class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    """
    Dependency function for FastAPI to get database session.

    BE-002: Auto-rollback on exception — if the route raises any exception
    (including AppError), uncommitted changes are rolled back before the
    session is closed. This eliminates the need for manual db.rollback()
    in every except block.

    Routes should call db.commit() on success. If they don't commit and
    no exception is raised, changes are NOT auto-committed (safe default).

    Usage:
        @app.get("/endpoint")
        def my_endpoint(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def get_db_context():
    """
    Context manager for database session in non-FastAPI code.
    Usage:
        with get_db_context() as db:
            result = db.query(Model).all()
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_database():
    """
    Initialize database connection check.

    Note: Table creation is handled by Alembic migrations in entrypoint.sh
    Seeding is handled in main.py lifespan:
    - System defaults via seed_defaults()
    - Stack templates via seed_stack_templates()
    """
    # Just verify database connection is working
    db = SessionLocal()
    try:
        # Simple connection test
        db.execute(text("SELECT 1"))
    finally:
        db.close()
