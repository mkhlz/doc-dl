from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

TARGETS = {
    "slim": {
        "windows-x64": ("doc-dl_win", ".zip"),
        "linux-x64": ("doc-dl_linux", ".tar.gz"),
        "macos-x64": ("doc-dl_macos_x64", ".tar.gz"),
        "macos-arm64": ("doc-dl_macos_arm64", ".tar.gz"),
    },
    "full": {
        "windows-x64": ("doc-dl_win_full", ".zip"),
        "linux-x64": ("doc-dl_linux_full", ".tar.gz"),
        "macos-x64": ("doc-dl_macos_x64_full", ".tar.gz"),
        "macos-arm64": ("doc-dl_macos_arm64_full", ".tar.gz"),
    },
}
VARIANTS = tuple(TARGETS)
CHROMIUM_DIRECTORY = re.compile(r"^chromium-(\d+)$")


def project_version(repository: Path) -> str:
    source = (repository / "src" / "doc_dl" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
    if not match:
        raise RuntimeError("Could not find doc-dl version")
    return match.group(1)


def safe_remove_tree(path: Path, parent: Path) -> None:
    resolved = path.resolve()
    allowed_parent = parent.resolve()
    if allowed_parent not in resolved.parents:
        raise RuntimeError(f"Refusing to remove build path outside {allowed_parent}: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def select_chromium_directory(browser_root: Path) -> Path:
    candidates: list[tuple[int, Path]] = []
    for path in browser_root.iterdir():
        match = CHROMIUM_DIRECTORY.fullmatch(path.name)
        if path.is_dir() and match:
            candidates.append((int(match.group(1)), path))
    if not candidates:
        raise RuntimeError(f"No full Chromium installation found in {browser_root}")
    return max(candidates, key=lambda item: item[0])[1]


def archive_bundle(bundle: Path, output: Path, variant: str, target: str) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    stem, extension = TARGETS[variant][target]
    asset = output / f"{stem}{extension}"
    asset.unlink(missing_ok=True)
    if extension == ".zip":
        created = Path(
            shutil.make_archive(
                str(asset.with_suffix("")),
                "zip",
                root_dir=bundle.parent,
                base_dir=bundle.name,
            )
        )
        if created != asset:
            raise RuntimeError(f"Unexpected archive path: {created}")
    else:
        with tarfile.open(asset, "w:gz") as archive:
            archive.add(bundle, arcname=bundle.name)
    return asset


def write_build_info(bundle: Path, target: str, variant: str, version: str) -> None:
    payload = {
        "format": 1,
        "name": "doc-dl",
        "target": target,
        "variant": variant,
        "chromium_bundled": variant == "full",
        "version": version,
    }
    (bundle / "BUILD_INFO.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build(
    target: str,
    variant: str,
    output: Path,
    work_root: Path,
    browser_source: Path | None = None,
) -> Path:
    repository = Path(__file__).resolve().parents[1]
    target_root = work_root / f"{target}-{variant}"
    safe_remove_tree(target_root, work_root)
    target_root.mkdir(parents=True)

    dist_root = target_root / "dist"
    pyinstaller_work = target_root / "pyinstaller-work"
    spec_root = target_root / "spec"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--onedir",
        "--name",
        "doc-dl",
        "--paths",
        str(repository / "src"),
        "--collect-all",
        "playwright",
        "--copy-metadata",
        "httpx",
        "--copy-metadata",
        "Pillow",
        "--copy-metadata",
        "playwright",
        "--copy-metadata",
        "pypdf",
        "--distpath",
        str(dist_root),
        "--workpath",
        str(pyinstaller_work),
        "--specpath",
        str(spec_root),
        str(repository / "scripts" / "frozen_entry.py"),
    ]
    subprocess.run(command, cwd=repository, check=True)

    bundle = dist_root / "doc-dl"
    executable = bundle / ("doc-dl.exe" if target.startswith("windows-") else "doc-dl")
    if not executable.is_file():
        raise RuntimeError(f"PyInstaller did not create {executable}")

    if variant == "full":
        if browser_source is None:
            raise RuntimeError("The 'full' variant requires a Playwright browser directory")
        browsers = browser_source.resolve()
        if not browsers.is_dir():
            raise RuntimeError(f"Playwright browser directory is missing: {browsers}")
        chromium = select_chromium_directory(browsers)
        bundled_browsers = bundle / "ms-playwright"
        bundled_browsers.mkdir()
        shutil.copytree(chromium, bundled_browsers / chromium.name)
    shutil.copy2(repository / "README.md", bundle / "README.md")
    write_build_info(bundle, target, variant, project_version(repository))

    # doc-dl doctor must succeed on a slim build with no Chromium present: browser
    # readiness is intentionally non-fatal there and is installed on first use.
    subprocess.run([str(executable), "version"], cwd=bundle, check=True)
    subprocess.run([str(executable), "doctor"], cwd=bundle, check=True)
    return archive_bundle(bundle, output, variant, target)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a portable doc-dl release bundle")
    parser.add_argument("--target", required=True, choices=sorted(TARGETS["slim"]))
    parser.add_argument(
        "--variant",
        default="slim",
        choices=VARIANTS,
        help="'slim' omits Chromium (default); 'full' bundles it for offline use",
    )
    parser.add_argument(
        "--browser-dir",
        type=Path,
        default=None,
        help=(
            "Playwright browser directory for the 'full' variant; "
            "defaults to PLAYWRIGHT_BROWSERS_PATH"
        ),
    )
    parser.add_argument("--output", type=Path, default=Path("release-assets"))
    parser.add_argument("--work-root", type=Path, default=Path("build") / "portable")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    browser_dir = args.browser_dir
    if args.variant == "full" and browser_dir is None:
        configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        if not configured:
            raise RuntimeError(
                "Set PLAYWRIGHT_BROWSERS_PATH or pass --browser-dir for --variant full"
            )
        browser_dir = Path(configured)
    asset = build(
        args.target,
        args.variant,
        args.output.resolve(),
        args.work_root.resolve(),
        browser_dir,
    )
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    print(f"Built {asset}")
    print(f"SHA-256 {digest}")
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as output:
            output.write(f"asset={asset}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
