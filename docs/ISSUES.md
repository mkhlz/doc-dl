# Field notes: issues, root causes, and fixes

A running record of real failures hit against live sites, what actually caused
them, and how they were fixed. Each entry keeps the original symptom so a
similar report can be recognised quickly next time.

The recurring lesson across these: **the reported symptom was rarely the real
cause**. Three separate entries here turned out to be something other than what
the error message said.

---

## 1. Scribd presentation URLs fell through to the generic provider

**Released in 0.1.4**

Symptom:

```
doc-dl "https://www.scribd.com/presentation/328976015/Divers-in-Islam"
Provider: generic
ERROR: The browser page did not expose document page containers for reconstruction
```

`ScribdProvider.match()` only recognised `/document/` and `/doc/` paths, so a
`/presentation/` URL scored zero and the generic provider won the registry
ranking. The generic provider has no knowledge of Scribd's viewer, so it found
nothing to reconstruct.

Verified against the live page that Scribd presentations use the *same*
`docManager` / `.outer_page` viewer as ordinary documents, so the only gap was
URL matching. Added `presentation` to the URL pattern.

Scribd's `/book/` and `/audiobook/` paths were deliberately **not** added:
ebooks and audiobooks now live on a separate site (Everand) behind a different
reader, and audio cannot be reconstructed into a PDF regardless.

---

## 2. Playwright teardown noise looked like a crash

**Released in 0.1.5**

Symptom, printed on otherwise healthy runs:

```
Task was destroyed but it is pending!
Future exception was never retrieved
playwright._impl._errors.TargetClosedError: Target page, context or browser has been closed
```

Playwright's sync API drives an asyncio connection on a background thread. When
that thread's event loop tears down, pending tasks log through the stdlib
`asyncio` logger. Confirmed harmless by observing the identical output during a
fully passing test run.

It was silenced (`logging.getLogger("asyncio").setLevel(logging.CRITICAL)` in
`browser.py`) purely because it read as a crash to users and buried the real
output.

---

## 3. Progress output was noisy, colourless, and broke when redirected

**Released in 0.1.5**

A 20-page document printed 60 lines of `Reconstructing/Capturing/Completed page
N/M`. Replaced with a single in-place updating progress bar, plus colour for
errors and completion.

Two bugs were found and fixed while building it:

- The bar emitted cursor-control escapes unconditionally, which garbles output
  when piped to a file or a log. Colour and in-place updates are now gated on
  `stdout` being a TTY, and honour `NO_COLOR` and `TERM=dumb`.
- The bar used Unicode block characters (`█`, `░`), which render as `?????`
  under the legacy code pages several Windows terminals still default to.
  Switched to plain ASCII `#` and `-`.

---

## 4. Orphaned `.part` files left in download folders

**Released in 0.1.5**

Interrupted or failed transfers leave `.doc-dl-<hash>.part` and matching
`.part.json` resume files behind by design, so a later run can resume. Nothing
cleaned them up when the download was abandoned for good.

Added `doc-dl clean [directory]`, which removes only files matching doc-dl's own
`.doc-dl-*.part` naming pattern and prompts before deleting unless `--yes` is
passed. It never touches files it does not recognise.

---

## 5. Viewer pages had "no measurable dimensions"

**Released in 0.1.6**

Symptom:

```
Reconstructing  [##----------------------]  1/12 pages (8%)
ERROR: A viewer page has no measurable dimensions
```

A first attempt assumed a transient layout race and added retry tolerance to the
bounding-box read. That was **wrong**, and the same document then failed with a
different error (`scroll_into_view_if_needed: Timeout... element is not visible`).

The actual cause was found by inspecting the live DOM during a stall: Scribd's
viewer virtualises offscreen pages and keeps them at inline `display: none`
*even once their images have fully loaded*. That style is only cleared by
Scribd's own scroll-position tracking, which never fires for pages jumped to
programmatically. Confirmed directly: `outer_page_2` had loaded images but
`display: none`, and forcing it visible gave it real dimensions immediately.

Fixed in `providers/scribd.py` by forcing the container visible once its content
is confirmed loaded. The earlier changes were kept as defence in depth:
`render.py` now scrolls an element into view *before* measuring it, and tolerates
a brief unmeasurable window.

Verified against three previously failing documents (12, 20 and 22 pages), all
of which then succeeded on the first attempt with no retries.

---

## 6. SlideShare was unsupported

**Released in 0.1.7**

SlideShare URLs fell through to the generic provider and failed with
`The browser page did not expose document page containers for reconstruction`.

Inspecting the page showed SlideShare embeds a `__NEXT_DATA__` JSON blob in the
**plain HTML** containing the exact slide count, title, image host, and the
available image widths. No JavaScript execution, and therefore no browser, is
needed at all.

This did not fit the existing screenshot-capture model, so a second
reconstruction strategy was added rather than bending the first:

| Strategy | Used by | Mechanism |
| --- | --- | --- |
| Screenshot capture | Scribd, generic | Render a DOM element per page in Chromium |
| Direct image fetch | SlideShare | Fetch already-known page image URLs over HTTP |

The shared PDF-assembly code was extracted into module-level helpers in
`render.py` so both strategies use it, and providers opt into the new strategy
through one optional hook (`Provider.image_pages_from_html`). Providers that do
not implement it are unaffected.

This mirrors how yt-dlp and gallery-dl structure per-site extractors: a class
per site, URL-pattern matching, ranked selection, generic fallback last.

---

## 7. A telemetry file was saved as if it were the document

**Released in 0.1.8. The most serious issue found so far.**

Symptom: exit code `0`, reported success, and a 323-byte `trace.txt` where a
presentation should have been.

```
[failed] image-reconstruction: A reconstructed page image could not be downloaded
Opening browser page for provider slideshare
[succeeded] browser-network: Downloaded and verified trace.txt
EXIT:0
```

The file was Cloudflare's `/cdn-cgi/trace` diagnostic endpoint
(`ip=... ts=... visit_scheme=https`). Because `text/plain` was in the accepted
document media types, *any* text response qualified as a document candidate and
passed verification. When the real strategy failed, the engine fell through to
the browser path and committed the first thing that looked acceptable.

This also explains stray `trace.txt` and `collect.txt` files appearing in
download folders. An earlier search of the source for those names found nothing
and wrongly concluded doc-dl did not create them; the names come from the
remote server, not from doc-dl's code.

Fix: `text/plain`, `text/markdown` and `text/csv` no longer qualify on media
type alone. They now require corroborating evidence: an explicit `attachment`
disposition, or a document extension on the URL. Binary document formats are
unaffected.

**A silent wrong answer is worse than a loud failure.** This class of bug is the
one to guard hardest against.

---

## 8. One transient page failure discarded the whole document

**Released in 0.1.8**

A 39-page deck failed at page 37 with no retry whatsoever, throwing away 36
already-rebuilt pages.

Added per-page retries (4 attempts, exponential backoff with jitter), following
yt-dlp's per-fragment retry model. Genuinely transient statuses (408, 425, 429,
500, 502, 503, 504) and connection errors are retried; permanent statuses are
not, because asking again will not change a 404.

---

## 9. Real errors were replaced by unrelated fallback errors

**Released in 0.1.8**

When reconstruction failed, the engine returned `None`, fell through to the
browser path, and surfaced *that* path's complaint instead. The user saw
`The browser page did not expose document page containers for reconstruction`
when the truth was a single page failing to download.

All three masking paths were closed. If a provider knew how to rebuild the
document and that attempt failed, its error is now what the user sees, and it
names the page: `Page 38 of 39 could not be downloaded`.

---

## 10. Sites announce more pages than they publish

**Released in 0.1.8**

Root cause of the original 39-page failure, and *not* a transient blip as first
assumed. SlideShare's own metadata claimed 39 slides while only 37 images
existed:

```
page 37: HTTP 200
page 38: HTTP 404   (at every resolution: 320, 638, 2048)
page 39: HTTP 404
```

No amount of retrying could fix stale metadata. Delivering 37 real pages is
worth far more than delivering nothing, but silently labelling 37 pages as a
complete document repeats the mistake of issue 7.

The resolution distinguishes two cases:

- **Missing from the end**: treated as a document that simply ends early.
  Truncate, and say so plainly:
  `Note: the site announced 39 pages but only published 37; saving the 37 pages that exist.`
- **Missing from the middle**: a real hole in the document. Confirmed by
  probing whether later pages exist. Still a hard failure.

A floor also applies: if fewer than half the announced pages survive, the
announced count is too far from reality to trust, and it fails rather than
delivering a fragment.

---

## 11. Release build stuck queued (infrastructure, not code)

Not a doc-dl bug, recorded so it is recognised rather than re-debugged.

A release workflow sat in `queued` for 15 minutes and then failed with:

```
The job was not acquired by Runner of type hosted even after multiple attempts
```

GitHub could not allocate a hosted runner. Verified the tag existed correctly,
Actions was enabled, and the workflow was active; nothing was wrong locally.

Re-running the workflow resolved it. A second, unrelated failure in the same
period was `Tag 0.1.7 already exists`, caused by pushing the tag manually *and*
asking the workflow to create it; deleting the tag and letting the workflow own
it fixed that.

If a release ever needs to bypass Actions entirely, a portable build can be
produced locally:

```bash
python scripts/build_portable.py --target windows-x64 --variant slim --output ./dist-local
```
