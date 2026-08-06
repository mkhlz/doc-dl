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

The portable release includes Python and every required library, so you do not
need to install Python yourself. There are two kinds of release:

- **Slim** (recommended): small download. The first time you open a site that
  needs a browser to extract its document, `doc-dl` automatically downloads
  Chromium once and reuses it after that. Direct file downloads never need
  Chromium at all.
- **Full**: a larger, offline-ready download with Chromium already included,
  for machines that are offline or on a locked-down network where an
  on-demand download would not work.

If you are not sure which one you need, use slim. The commands below install
the slim release by default.

### Windows

Open PowerShell and run:

```powershell
irm https://raw.githubusercontent.com/mkhlz/doc-dl/master/scripts/install.ps1 | iex
```

The installer downloads the latest slim Windows release, verifies its SHA-256
checksum, installs it under your local application-data folder, and adds
`doc-dl` to your user `PATH`. To install the full offline build instead, set
`DOC_DL_VARIANT` first:

```powershell
$env:DOC_DL_VARIANT = "full"
irm https://raw.githubusercontent.com/mkhlz/doc-dl/master/scripts/install.ps1 | iex
```

### macOS or Linux

```sh
curl -fsSL https://raw.githubusercontent.com/mkhlz/doc-dl/master/scripts/install.sh | sh
```

The installer selects the right slim release for Linux x64, Intel macOS, or
Apple Silicon, verifies its checksum, and links `doc-dl` into `~/.local/bin`.
For the full offline build instead:

```sh
curl -fsSL https://raw.githubusercontent.com/mkhlz/doc-dl/master/scripts/install.sh | DOC_DL_VARIANT=full sh
```

### Uninstall

Remove the installed program and its PATH entry:

```powershell
irm https://raw.githubusercontent.com/mkhlz/doc-dl/master/scripts/uninstall.ps1 | iex
```

On macOS or Linux:

```sh
curl -fsSL https://raw.githubusercontent.com/mkhlz/doc-dl/master/scripts/uninstall.sh | sh
```

Both commands preserve isolated sign-in profiles, any Chromium runtime
`doc-dl` downloaded on demand, and other runtime state. To remove that state
too (sign-in sessions and any downloaded Chromium runtime together), use one
of these explicit commands:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/mkhlz/doc-dl/master/scripts/uninstall.ps1))) -PurgeData
```

```sh
curl -fsSL https://raw.githubusercontent.com/mkhlz/doc-dl/master/scripts/uninstall.sh | sh -s -- --purge-data
```

To remove only the downloaded Chromium runtime and keep your sign-in profiles,
use `doc-dl uninstall-browser` instead; see
[Managing the Chromium browser runtime](#managing-the-chromium-browser-runtime).

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

### Try it with real sites

Try these in PowerShell:

- **W3C direct PDF**, quick smoke test (a plain file download, no browser
  needed):

  ```powershell
  doc-dl "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"
  ```

- **Internet Archive public-domain PDF**:

  ```powershell
  doc-dl "https://ia801907.us.archive.org/17/items/librivoxcdcoverart36/bestfriend_2006.pdf"
  ```

- **arXiv research paper PDF**:

  ```powershell
  doc-dl "https://arxiv.org/pdf/2212.07286"
  ```

- **Scribd multi-page viewer test**: this is the case that needs Chromium,
  so it is also a good way to see the one-time browser download in action on
  a fresh install:

  ```powershell
  doc-dl "https://www.scribd.com/document/959028055/Ten-Page-Sample" --verbose
  ```

  Scribd downloads depend on the access available through your normal Scribd
  account; the example above is a public
  [Scribd sample](https://www.scribd.com/document/959028055/Ten-Page-Sample)
  that does not require signing in.

Use this pattern to keep files in your Downloads folder instead of the
current directory:

```powershell
doc-dl "PASTE-URL-HERE" --output "$HOME\Downloads" --verbose
```

## Other installation methods

<a id="release-files"></a>

### Download a release archive manually

Open the [GitHub Releases](https://github.com/mkhlz/doc-dl/releases) page and
download the archive for your computer. Extract the `doc-dl` folder somewhere
permanent and add that folder to `PATH`.

**Slim (recommended)**: Python and every required library, but no Chromium.
The first browser-backed download triggers a one-time Chromium download; plain
file downloads never need it.

| Release file | System |
| --- | --- |
| `doc-dl_win.zip` | Windows 10 or newer, Intel or AMD 64-bit |
| `doc-dl_linux.tar.gz` | 64-bit Linux |
| `doc-dl_macos_x64.tar.gz` | Intel Mac |
| `doc-dl_macos_arm64.tar.gz` | Apple Silicon Mac |

**Full**: everything in slim, plus Chromium already included, for offline use
or locked-down networks. Noticeably larger.

| Release file | System |
| --- | --- |
| `doc-dl_win_full.zip` | Windows 10 or newer, Intel or AMD 64-bit |
| `doc-dl_linux_full.tar.gz` | 64-bit Linux |
| `doc-dl_macos_x64_full.tar.gz` | Intel Mac |
| `doc-dl_macos_arm64_full.tar.gz` | Apple Silicon Mac |

`SHA2-256SUMS` in the same release lists SHA-256 checksums for every archive
above, slim and full alike. During a workflow run, GitHub also displays
temporary `build-bin-*` artifacts that transfer these archives between jobs;
the final GitHub Release shows the public filenames in the tables above.

### Install from a Python wheel

Python users can keep using the smaller wheel:

```powershell
python -m pip install .\dist\doc_dl-0.1.7-py3-none-any.whl
doc-dl doctor
```

This method requires Python 3.11 or newer. Chromium is not installed yet;
`doc-dl` downloads it automatically the first time a browser-backed site needs
it, or you can install it ahead of time with `doc-dl install-browser`.

### Install for development

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\doc-dl.exe doctor
```

On macOS and Linux, use `.venv/bin/python` and `.venv/bin/doc-dl` instead. To
run the full test suite, including the browser-backed tests, also install
Chromium once:

```powershell
.\.venv\Scripts\python.exe -m playwright install --no-shell chromium
```

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
doc-dl install-browser
doc-dl uninstall-browser
```

`doctor` checks Python, the required libraries, whether Chromium is installed,
and the writable application-state location. It reports Chromium's status but
does not fail because of it: a missing Chromium is expected for a fresh slim
install and is not a problem until a browser-backed site actually needs it.

<a id="managing-the-chromium-browser-runtime"></a>

## Managing the Chromium browser runtime

You never have to think about Chromium for direct file downloads; those never
touch a browser. The first time a browser-backed site does need one, `doc-dl`
downloads Chromium automatically, shows progress, and reuses it for every
download after that:

```powershell
doc-dl "https://example.com/some-javascript-viewer"
# Chromium is not installed. Downloading the browser runtime now
# (one-time download into C:\Users\you\AppData\Local\doc-dl\browsers)...
```

To install it ahead of time instead of waiting for the first browser-backed
download:

```powershell
doc-dl install-browser
```

To remove the downloaded browser runtime later and reclaim the disk space,
without touching your sign-in profiles:

```powershell
doc-dl uninstall-browser
```

Chromium is stored in a stable per-user location (`doc-dl doctor` reports the
exact path), never beside the installed program and never inside a release
archive. `--no-browser` still skips browser escalation entirely, so it also
skips this download. A full offline release already has Chromium bundled, so
`install-browser` and `uninstall-browser` report it as already installed;
`uninstall-browser` refuses to remove a bundled offline copy, since that would
defeat the purpose of choosing the full build.

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

For a local slim build, install the build extras and run the builder for the
current operating system. `--variant slim` is the default, so it can be
omitted:

```powershell
python -m pip install ".[build]"
python scripts\build_portable.py --target windows-x64 --variant slim
```

For a local full build, also place Chromium in a dedicated directory first:

```powershell
python -m pip install ".[build]"
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\build\playwright-browsers"
python -m playwright install --no-shell chromium
python scripts\build_portable.py --target windows-x64 --variant full
```

The resulting archive is written to `release-assets/`. The builder runs both
`doc-dl version` and `doc-dl doctor` against the frozen executable before it
creates the archive; `doc-dl doctor` passing without Chromium is expected and
required for a slim build. The GitHub Actions release workflow builds both
variants for every platform in the same run.

## Provider support

- `generic`: direct files, redirects, page-source discovery, JavaScript
  downloads, document responses, and compatible viewer reconstruction.
- `scribd`: normalized document URLs, isolated login profiles, embedded viewer
  activation, lazy page loading, and complete-page PDF reconstruction.
- `slideshare`: presentation slide URLs parsed directly from the page, no
  browser required; every slide is fetched and merged into one PDF.

Websites can change their layouts or restrict access. In those cases,
`doc-dl` returns a stable error and leaves no unverified final file behind.

## License

This project is licensed under the [MIT License](LICENSE).
