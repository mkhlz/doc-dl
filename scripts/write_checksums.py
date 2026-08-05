from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

CHECKSUM_FILE_NAME = "SHA2-256SUMS"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksums(directory: Path) -> Path:
    directory = directory.resolve()
    (directory / "SHA256SUMS").unlink(missing_ok=True)
    assets = sorted(
        path for path in directory.iterdir() if path.is_file() and path.name != CHECKSUM_FILE_NAME
    )
    if not assets:
        raise RuntimeError(f"No release assets found in {directory}")
    lines = [f"{sha256(path)}  {path.name}" for path in assets]
    checksum_file = directory / CHECKSUM_FILE_NAME
    checksum_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return checksum_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Write SHA-256 checksums for release assets")
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    checksum_file = write_checksums(args.directory)
    print(checksum_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
