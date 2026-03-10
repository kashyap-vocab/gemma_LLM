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
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            # 1. Create sequence FIRST so CREATE TABLE can reference it
            conn.execute(text("CREATE SEQUENCE IF NOT EXISTS customer_id_seq START 1"))
            conn.commit()

        # 2. Create all tables (customer.id is plain BIGINT here, no DEFAULT yet)
        Base.metadata.create_all(bind=engine)

        # 3. Wire the sequence as the column default
        with engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE customer ALTER COLUMN id SET DEFAULT nextval('customer_id_seq')"
            ))
            conn.commit()

        print("Database tables and sequences created successfully.")
    except Exception as exc:
        print(f"Error creating tables: {exc}")
        raise


if __name__ == "__main__":
    init_db()
