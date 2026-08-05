from __future__ import annotations

from doc_dl.runtime import configure_bundled_runtime

configure_bundled_runtime()

from doc_dl.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
