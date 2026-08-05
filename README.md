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

## Requirements

- Python 3.11 or newer
- Playwright Chromium
- Windows, macOS, or Linux

## Quickstart

### Install from a wheel

```powershell
python -m pip install .\dist\doc_dl-0.1.2-py3-none-any.whl
python -m playwright install chromium
doc-dl doctor
```

### Install for development

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\doc-dl.exe doctor
```

On macOS and Linux, use `.venv/bin/python` and `.venv/bin/doc-dl` instead.

### Download one URL right away

```powershell
doc-dl -Url "https://www.scribd.com/document/1039114955/GMAT-Syllabus-PDF"
```

The normal URL form also works:

```powershell
doc-dl "https://www.scribd.com/document/1039114955/GMAT-Syllabus-PDF"
```

If PowerShell cannot find `doc-dl`, call the executable inside the active
virtual environment directly:

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
python -m playwright install chromium
```

## Development checks

```powershell
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pip wheel --no-deps . --wheel-dir dist
```

## Provider support

- `generic`: direct files, redirects, page-source discovery, JavaScript
  downloads, document responses, and compatible viewer reconstruction.
- `scribd`: normalized document URLs, isolated login profiles, embedded viewer
  activation, lazy page loading, and complete-page PDF reconstruction.

Websites can change their layouts or restrict access. In those cases,
`doc-dl` returns a stable error and leaves no unverified final file behind.
