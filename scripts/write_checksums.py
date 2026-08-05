from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Write SHA-256 checksums for release assets")
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    directory = args.directory.resolve()
    assets = sorted(
        path for path in directory.iterdir() if path.is_file() and path.name != "SHA256SUMS"
    )
    if not assets:
        raise RuntimeError(f"No release assets found in {directory}")
    lines = [f"{sha256(path)}  {path.name}" for path in assets]
    checksum_file = directory / "SHA256SUMS"
    checksum_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(checksum_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
