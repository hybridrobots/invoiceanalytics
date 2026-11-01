"""Allow: python -m invoice_intelligence"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    run_py = root / "run.py"
    if not run_py.exists():
        print(f"Launcher not found: {run_py}", file=sys.stderr)
        return 1
    # Execute run.py as __main__
    sys.path.insert(0, str(root))
    runpy.run_path(str(run_py), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
