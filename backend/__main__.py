"""Entry point: `python -m backend --text "..." | --url https://... [--debug]`."""

from .s1_claims import _main

if __name__ == "__main__":
    raise SystemExit(_main())
