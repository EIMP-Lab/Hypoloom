"""Compatibility entry point for the read-only model source inspector."""
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from hypoloom.model_options import main

if __name__ == "__main__":
    raise SystemExit(main(repository=Path(__file__).resolve().parents[4]))
