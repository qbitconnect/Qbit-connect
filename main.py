import os
import sys
from pathlib import Path

# Add backend directory to sys.path so app modules import cleanly
root_dir = Path(__file__).resolve().parent
backend_dir = root_dir / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Strip empty string environment variables injected by PaaS/Pydantic
for k in list(os.environ.keys()):
    if os.environ[k] == "":
        del os.environ[k]

from app.main import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
