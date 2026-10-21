"""Compatibility entry point; prefer ``python -m robopii``."""
from robopii.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
