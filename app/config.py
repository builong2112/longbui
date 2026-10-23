"""Settings read from environment variables (or a .env file in the project root)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")

TEMPLATES_DIR = Path(os.getenv("IPC_TEMPLATES_DIR", BASE_DIR / "templates"))
DATA_DIR = Path(os.getenv("IPC_DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "ipc.db"

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "medium")
SECRET_KEY = os.getenv("IPC_SECRET_KEY", "change-me-in-production")

# Ảnh được thu nhỏ về cạnh dài tối đa này trước khi gửi AI (đủ nét để đọc chữ tay, giảm chi phí).
MAX_IMAGE_SIDE = int(os.getenv("IPC_MAX_IMAGE_SIDE", "2000"))
PDF_DPI = int(os.getenv("IPC_PDF_DPI", "200"))
