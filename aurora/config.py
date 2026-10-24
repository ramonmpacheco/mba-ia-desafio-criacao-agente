import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    state_dir: Path
    data_dir: Path
    model: str

    @classmethod
    def load(cls):
        load_dotenv(ROOT / ".env")
        return cls(
            Path(os.getenv("AURORA_STATE_DIR") or ROOT / ".state").resolve(),
            ROOT / "dados",
            os.getenv("GEMINI_MODEL") or "gemini-3.1-flash-lite",
        )
