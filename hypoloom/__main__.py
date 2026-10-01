"""Support the same CLI through ``python -m hypoloom``."""
from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
