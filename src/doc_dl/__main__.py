from __future__ import annotations

from doc_dl.cli import main
from doc_dl.runtime import configure_browsers_path

if __name__ == "__main__":
    configure_browsers_path()
    main()
