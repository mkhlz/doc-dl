# doc-dl

`doc-dl` is a command-line downloader for documents that do not always behave
like ordinary download links. Give it a public document or landing-page URL and
it works through the sensible options: direct download first, then page
discovery, browser downloads, and finally a verified PDF reconstruction when a
viewer is the only available public representation.

It is built for the slightly annoying reality of modern document sites: pages
that hide the file behind JavaScript, viewers that lazy-load pages, redirects,
and downloads that fail halfway through. The goal is simple: get a usable,
verified file or get a clear reason why that was not possible.

## What it can do

- Download PDFs, Office documents, EPUBs, OpenDocument files, RTF, CSV, and
  plain text when a site exposes a real document response.
- Find document URLs embedded in links, metadata, JSON-LD, and page source.
- Watch browser downloads and document responses on JavaScript-heavy sites.
- Reconstruct a complete, image-backed PDF from visible viewer pages when no
  original file is available.
- Resume interrupted HTTP downloads when the server provides safe validators.
- Verify files before saving them to their final name.
- Keep sign-in cookies in an isolated browser profile, never in command-line
  arguments.

## Quickstart

The portable release includes Python, every required library, and Chromium. You
do not need to install Python or Playwright.

### Windows

Open PowerShell and run:

```powershell
irm https://raw.githubusercontent.com/mkhlz/doc-dl/master/scripts/install.ps1 | iex
```

The installer downloads the latest Windows release, verifies its SHA-256
checksum, installs it under your local application-data folder, and adds
`doc-dl` to your user `PATH`.

### macOS or Linux

```sh
curl -fsSL https://raw.githubusercontent.com/mkhlz/doc-dl/master/scripts/install.sh | sh
```

The installer selects the right release for Linux x64, Intel macOS, or Apple
Silicon, verifies its checksum, and links `doc-dl` into `~/.local/bin`.

### Uninstall

Remove the installed program and its PATH entry:

```powershell
irm https://raw.githubusercontent.com/mkhlz/doc-dl/master/scripts/uninstall.ps1 | iex
```

On macOS or Linux:

```sh
curl -fsSL https://raw.githubusercontent.com/mkhlz/doc-dl/master/scripts/uninstall.sh | sh
```

Both commands preserve isolated sign-in profiles and other runtime state. To
remove that state too, including saved browser sessions, use one of these
explicit commands:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/mkhlz/doc-dl/master/scripts/uninstall.ps1))) -PurgeData
```

```sh
curl -fsSL https://raw.githubusercontent.com/mkhlz/doc-dl/master/scripts/uninstall.sh | sh -s -- --purge-data
```

### Download a document

Open a new terminal after installation and give `doc-dl` a URL:

```powershell
doc-dl "https://www.scribd.com/document/1039114955/GMAT-Syllabus-PDF"
```

PowerShell users can also use the winget-style `-Url` form:

```powershell
doc-dl -Url "https://www.scribd.com/document/1039114955/GMAT-Syllabus-PDF"
```

That is the whole everyday workflow. Run `doc-dl doctor` if you ever want to
check the installation.

## Other installation methods

<a id="release-files"></a>

### Download a release archive manually

Open the [GitHub Releases](https://github.com/mkhlz/doc-dl/releases) page and
download the archive for your computer. Extract the `doc-dl` folder somewhere
permanent and add that folder to `PATH`.

| Release file | System |
| --- | --- |
| `doc-dl_win.zip` | Windows 10 or newer, Intel or AMD 64-bit |
| `doc-dl_linux.tar.gz` | 64-bit Linux |
| `doc-dl_macos_x64.tar.gz` | Intel Mac |
| `doc-dl_macos_arm64.tar.gz` | Apple Silicon Mac |
| `SHA2-256SUMS` | SHA-256 checksums for every archive |

Portable archives are larger than the Python wheel because they contain a
matching Chromium browser. This is what lets browser-backed downloads work
without asking the user to install anything else. Sizes of a few hundred
megabytes are expected. During a workflow run, GitHub also displays temporary
`build-bin-*` artifacts that transfer these archives between jobs; the final
GitHub Release shows the public filenames in the table above.

### Install from a Python wheel

Python users can keep using the smaller wheel:

```powershell
python -m pip install .\dist\doc_dl-0.1.2-py3-none-any.whl
python -m playwright install --no-shell chromium
doc-dl doctor
```

This method requires Python 3.11 or newer.

### Install for development

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m playwright install --no-shell chromium
.\.venv\Scripts\doc-dl.exe doctor
```

On macOS and Linux, use `.venv/bin/python` and `.venv/bin/doc-dl` instead.

If the virtual environment is not activated, call its executable directly:

```powershell
.\.venv\Scripts\doc-dl.exe -Url "https://www.scribd.com/document/1039114955/GMAT-Syllabus-PDF"
```

## Everyday downloads

Save into a downloads folder and record redacted provenance metadata:

```powershell
doc-dl URL --output .\downloads --write-metadata --verbose
```

Choose one exact output path:

```powershell
doc-dl URL --output .\downloads\my-document.pdf
```

Use a filename pattern. Available fields are `{title}`, `{ext}`, `{provider}`
and `{filename}`:

```powershell
doc-dl URL --output .\downloads --filename "{provider}-{title}.{ext}"
```

## Download options

| Option | What it does |
| --- | --- |
| `--output`, `-o` | Output directory or exact file path. |
| `--filename` | Filename template for downloads saved to a directory. |
| `--original-only` | Accept only original document bytes. Never use a rendered PDF. |
| `--no-browser` | Skip browser discovery and viewer reconstruction. |
| `--profile NAME` | Use an isolated signed-in browser profile. |
| `--timeout 5m` | Set the full operation timeout. Supports seconds, minutes, and hours. |
| `--retries 5` | Retry transient transfer failures. |
| `--no-resume` | Disable protected resume for interrupted HTTP transfers. |
| `--overwrite` | Replace an existing output file. |
| `--write-metadata` | Save a redacted `.doc-dl.json` provenance sidecar. |
| `--verbose` | Show which strategies are being tried. |
| `--json` | Emit newline-delimited JSON events for automation. |
| `--quiet` | Print only errors and the final output path. |

## When a login is needed

For documents your normal provider account can access, create an isolated
profile and finish sign-in in the browser window:

```powershell
doc-dl login scribd --profile personal
doc-dl URL --profile personal
```

Remove that profile when you are finished:

```powershell
doc-dl logout scribd --profile personal --yes
```

## How results are handled

`doc-dl` prefers an original file whenever one is available. If a site offers
only a visible viewer, it can create a reconstructed PDF from the pages the
viewer actually displays. Successful results identify their source as
`original`, `exported`, `reconstructed`, or `printed` in the metadata.

The tool rejects empty files, HTML masquerading as a document, corrupt PDFs,
invalid Office containers, and visibly blank reconstructed pages. A partial or
unverified result is not promoted to the final output path.

## Useful commands

```powershell
doc-dl version
doc-dl providers
doc-dl doctor
```

`doctor` checks Python, the required libraries, Chromium, and the writable
application-state location. If Chromium is missing, run:

```powershell
python -m playwright install --no-shell chromium
```

## Development checks

```powershell
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pip wheel --no-deps . --wheel-dir dist
```

## Building portable releases

The release workflow lives in `.github/workflows/release.yml` and follows the
same build, collect, tag, and publish sequence for every entry point:

- Run **Release** manually with the version field empty to build and verify all
  packages without creating a tag or GitHub Release.
- Run **Release** manually with a bare version such as `0.1.3` to verify that it
  matches the package, build the packages, push the tag, and publish the release.
- Push a bare version tag such as `0.1.3` to run the same build and publish path.

Before tagging, update the version in `pyproject.toml` and
`src/doc_dl/__init__.py`, run the development checks, and commit the release
changes. The tag should be the exact version without a `v` prefix.

For a local portable build, install the build extras, place Chromium in a
dedicated directory, and run the builder for the current operating system:

```powershell
python -m pip install ".[build]"
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\build\playwright-browsers"
python -m playwright install --no-shell chromium
python scripts\build_portable.py --target windows-x64
```

The resulting archive is written to `release-assets/`. The builder runs both
`doc-dl version` and `doc-dl doctor` against the frozen executable before it
creates the archive.

## Provider support

- `generic`: direct files, redirects, page-source discovery, JavaScript
  downloads, document responses, and compatible viewer reconstruction.
- `scribd`: normalized document URLs, isolated login profiles, embedded viewer
  activation, lazy page loading, and complete-page PDF reconstruction.

Websites can change their layouts or restrict access. In those cases,
`doc-dl` returns a stable error and leaves no unverified final file behind.

## License

This project is licensed under the [MIT License](LICENSE).
