"""Central repo-root path so modules don't each re-derive it from their own depth."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
