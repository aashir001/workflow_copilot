from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("COPILOT_DATA_DIR", ROOT / "data")))
    db_path: str = field(default_factory=lambda: os.getenv("COPILOT_DB", str(ROOT / "copilot.db")))
    planner: str = field(default_factory=lambda: os.getenv("COPILOT_PLANNER", "local"))
    failure_rate: float = field(default_factory=lambda: _f("COPILOT_FAILURE_RATE", 0.0))
    seed: int = 0

    # Routing thresholds (retrieval similarity, 0..1). Below -> hand off to a human.
    min_score: float = field(default_factory=lambda: _f("COPILOT_MIN_SCORE", 0.12))
    min_margin: float = field(default_factory=lambda: _f("COPILOT_MIN_MARGIN", 0.02))

    # Tool-call retry policy
    max_attempts: int = 3
    retry_base_delay_s: float = field(default_factory=lambda: _f("COPILOT_RETRY_DELAY_S", 0.01))

    # LLM planners
    anthropic_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
    openai_base_url: str = field(default_factory=lambda: os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1"))
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "llama3.1"))

    @property
    def sop_dir(self) -> Path:
        return self.data_dir / "sops"
