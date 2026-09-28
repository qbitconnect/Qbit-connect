import os
import sys
from pathlib import Path

# Add backend directory to sys.path so app modules import cleanly
root_dir = Path(__file__).resolve().parent.parent
backend_dir = root_dir / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Strip empty string environment variables injected by Vercel/PaaS
for k in list(os.environ.keys()):
    if os.environ[k] == "":
        del os.environ[k]

# Configure Vercel serverless environment defaults
if os.environ.get("VERCEL") or not os.environ.get("DATABASE_URL"):
    os.environ.setdefault("QBIT_DATA_DIR", "/tmp/qbit-data")
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////tmp/qbit-dev.db")
    if os.environ.get("DATABASE_URL", "").startswith("sqlite"):
        os.environ["QBIT_ENV"] = "staging"
    os.environ.setdefault("QBIT_EMBEDDED_WORKER_ENABLED", "false")
    os.environ.setdefault("QBIT_COOKIE_SECURE", "true")
    os.environ.setdefault("QBIT_SECRET_KEY", "vercel-production-secret-key-at-least-32-chars-long")
    os.environ.setdefault("EMAIL_WEBHOOK_SECRET", "vercel-default-email-webhook-secret-key-32b")
    os.environ.setdefault("WHATSAPP_WEBHOOK_SECRET", "vercel-default-whatsapp-webhook-secret-32b")
    os.environ.setdefault("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "vercel-default-whatsapp-verify-token")

from app.main import create_app

app = create_app()

@app.on_event("startup")
async def _init_serverless_db():
    """Ensure database schema and initial administrator exist on cold start."""
    try:
        import uuid
        from sqlalchemy import select
        from app.core.security import hash_password
        from app.db.base import Base
        from app.models.user import User

        async with app.state.db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with app.state.db.session() as session:
            admin = await session.scalar(select(User).where(User.email == "admin@qbit.internal"))
            if not admin:
                new_admin = User(
                    id=uuid.uuid4(),
                    email="admin@qbit.internal",
                    password_hash=hash_password("admin123"),
                    full_name="System Administrator",
                    role="ADMIN",
                    is_active=True,
                )
                session.add(new_admin)
                await session.commit()
    except Exception as exc:
        import logging
        logging.getLogger("qbit.serverless").warning(f"Serverless DB init warning: {exc}")
