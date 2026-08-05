from __future__ import annotations

from doc_dl.runtime import configure_browsers_path

configure_browsers_path()

from doc_dl.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
