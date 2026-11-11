"""Initialize PostgreSQL database schema.

Creates all tables defined in app/db/models.py.
Run this after provisioning a new database instance.
"""
from __future__ import annotations

import sys

from app.config import Settings
from app.db.session import init_db, make_engine


def main():
    settings = Settings()
    
    if settings.storage_backend != "postgres":
        print(f"❌ STORAGE_BACKEND is '{settings.storage_backend}', expected 'postgres'")
        print("   Set STORAGE_BACKEND=postgres in your .env file")
        return 1
    
    if not settings.database_url or settings.database_url == "postgresql+psycopg://user:password@localhost:5432/sentiment_agent":
        print("❌ DATABASE_URL not configured or still using placeholder")
        print("   Set DATABASE_URL in your .env file to your PostgreSQL connection string")
        return 1
    
    print("Initializing database schema...")
    print(f"Database URL: {settings.database_url.split('@')[0]}@...")  # Hide password
    
    try:
        engine = make_engine(settings.database_url)
        init_db(engine)
        print("✅ Database schema initialized successfully")
        print("   All tables created")
        return 0
    except Exception as exc:
        print(f"❌ Failed to initialize database: {exc}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
