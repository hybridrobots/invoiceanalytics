"""Invoice Intelligence — local Flask web UI entry point."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from invoice_intelligence.web import create_app

app = create_app()


def main(host: str = "127.0.0.1", port: int = 8501, open_browser: bool = True) -> None:
    if open_browser:
        import threading
        import time
        import webbrowser

        def _open() -> None:
            time.sleep(0.8)
            webbrowser.open(f"http://{host}:{port}/")

        threading.Thread(target=_open, daemon=True).start()

    # threaded=False avoids some desktop popup/reload quirks; debug off for normal use
    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
