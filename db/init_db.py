"""
Initialize database by creating all tables from ORM models.
"""
import os
from dotenv import load_dotenv
from db.database import engine, Base
from db.models import Conversation, CustomerFeedbackData, CustomerData, CallMetadata, ActiveCallContext

load_dotenv()

def init_db():
    """Create all database tables from ORM models."""
    print("🔄 Creating database tables from ORM models...")
    
    # Import all models so they're registered with Base
    # (already imported above, but being explicit)
    
    try:
        Base.metadata.create_all(bind=engine)
        print("✅ Database tables created successfully!")
    except Exception as e:
        print(f"❌ Error creating tables: {e}")
        raise


if __name__ == "__main__":
    init_db()
