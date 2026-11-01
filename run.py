#!/usr/bin/env python3
"""Launch Invoice Intelligence with: python run.py"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    parser = argparse.ArgumentParser(description="Invoice Intelligence local server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser tab")
    args = parser.parse_args()

    try:
        from invoice_intelligence.app import main as run_app
    except ImportError as exc:
        print(
            "Dependencies missing.\n"
            "On Debian/Ubuntu use:\n"
            "  ./run.sh\n"
            f"Details: {exc}",
            file=sys.stderr,
        )
        return 1

    print(f"Invoice Intelligence → http://{args.host}:{args.port}/")
    run_app(host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
