"""Compatibility entry point for the shared managed GPU pool implementation."""
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from hypoloom.managed_gpu_pool import main

if __name__ == "__main__":
    raise SystemExit(main())
