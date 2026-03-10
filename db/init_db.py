"""
Initialize the database by creating all tables defined in the ORM models.

Run directly:
    python -m db.init_db
"""
from dotenv import load_dotenv

from db.database import engine, Base

# Import every model so SQLAlchemy's metadata registry is fully populated
# before create_all() is called.
from db.models import Customer, CallMetadata, Conversation, CustomerFeedback  # noqa: F401

load_dotenv()


def init_db() -> None:
    """Create all database tables from ORM models (no-op if they already exist)."""
    print("Creating database tables...")
    try:
        Base.metadata.create_all(bind=engine)
        print("Database tables created successfully.")
    except Exception as exc:
        print(f"Error creating tables: {exc}")
        raise


if __name__ == "__main__":
    init_db()