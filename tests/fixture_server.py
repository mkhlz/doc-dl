from __future__ import annotations

import io
import socket
import threading
import zipfile
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PIL import Image
from pypdf import PdfWriter

FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def make_jpeg(*, color: str = "blue") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (200, 260), color).save(output, format="JPEG")
    return output.getvalue()


def make_pdf(*, padding: int = 0) -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    metadata = {"/Title": "doc-dl fixture PDF"}
    if padding:
        metadata["/FixturePadding"] = "x" * padding
    writer.add_metadata(metadata)
    writer.write(output)
    return output.getvalue()


def make_docx() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
              <Default Extension="xml" ContentType="application/xml"/>
              <Override PartName="/word/document.xml"
                ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
            </Types>""",
        )
        archive.writestr(
            "word/document.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
              <w:body><w:p><w:r><w:t>doc-dl fixture</w:t></w:r></w:p></w:body>
            </w:document>""",
        )
    return output.getvalue()


PDF_BYTES = make_pdf()
RESUME_PDF_BYTES = make_pdf(padding=400_000)
DOCX_BYTES = make_docx()
JPEG_BYTES = make_jpeg()


class FixtureState:
    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.lock = threading.Lock()

    def increment(self, key: str) -> int:
        with self.lock:
            self.counts[key] += 1
            return self.counts[key]


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def state(self) -> FixtureState:
        return self.server.fixture_state

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        self.state.increment(path)

        if path == "/files/sample.pdf":
            self._send_document(
                PDF_BYTES,
                "application/pdf",
                'attachment; filename="sample.pdf"',
            )
            return
        if path == "/opaque/document/42":
            self._send_document(
                DOCX_BYTES,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                'attachment; filename="fixture-document.docx"',
            )
            return
        if path == "/redirect/pdf":
            self._redirect("/redirect/pdf-two")
            return
        if path == "/redirect/pdf-two":
            self._redirect("/files/sample.pdf")
            return
        if path.startswith("/site/"):
            fixture = (FIXTURE_ROOT / "site" / Path(path).name).resolve()
            site_root = (FIXTURE_ROOT / "site").resolve()
            if site_root not in fixture.parents or not fixture.is_file():
                self._send_bytes(404, b"not found", "text/plain")
                return
            self._send_bytes(200, fixture.read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/browser/download/sample.pdf":
            self._send_document(
                PDF_BYTES,
                "application/pdf",
                'attachment; filename="browser-download.pdf"',
            )
            return
        if path == "/xhr/document":
            self._send_document(PDF_BYTES, "application/pdf", 'inline; filename="xhr.pdf"')
            return
        if path == "/telemetry/status":
            self._send_document(b"", "text/plain", 'attachment; filename="status"')
            return
        if path.startswith("/viewer/pages/") and path.endswith(".svg"):
            page_number = Path(path).stem
            svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="760" height="1000">
              <rect width="760" height="1000" fill="white"/>
              <text x="380" y="500" text-anchor="middle" font-family="sans-serif"
                font-size="72" fill="#172033">Page {page_number}</text>
            </svg>""".encode()
            self._send_bytes(200, svg, "image/svg+xml")
            return
        if path == "/auth/document":
            cookie = self.headers.get("Cookie", "")
            if "fixture-session=valid" in cookie:
                self._send_document(PDF_BYTES, "application/pdf", 'attachment; filename="auth.pdf"')
            else:
                body = b"""<!doctype html><html><body><form action="/auth/login">
                <h1>Sign in required</h1><input type="password"></form></body></html>"""
                self._send_bytes(200, body, "text/html; charset=utf-8")
            return
        if path == "/retry/rate-limit":
            if self.state.counts[path] == 1:
                self._send_bytes(429, b"retry", "text/plain", {"Retry-After": "0"})
            else:
                self._send_document(
                    PDF_BYTES,
                    "application/pdf",
                    'attachment; filename="retried.pdf"',
                )
            return
        if path == "/resume/pdf":
            self._handle_resume_pdf()
            return
        if path == "/errors/fake.pdf":
            body = b"<!doctype html><html><body><h1>Sign in</h1></body></html>"
            self._send_bytes(200, body, "application/pdf")
            return
        if path == "/errors/corrupt.pdf":
            self._send_bytes(200, b"%PDF-1.7\nthis is not a complete PDF", "application/pdf")
            return
        if path.startswith("/imageset/pages/") and path.endswith(".jpg"):
            self._send_bytes(200, JPEG_BYTES, "image/jpeg")
            return
        if path == "/imageset/flaky-page.jpg":
            if self.state.counts[path] == 1:
                self._send_bytes(503, b"unavailable", "text/plain")
            else:
                self._send_bytes(200, JPEG_BYTES, "image/jpeg")
            return
        if path == "/imageset/missing-page.jpg":
            self._send_bytes(404, b"not found", "text/plain")
            return
        if path == "/imageset/always-unavailable.jpg":
            self._send_bytes(503, b"unavailable", "text/plain")
            return
        if path.startswith("/imageset/truncated/"):
            # Announces more pages than it publishes: pages 1-3 exist, the
            # rest are permanently absent, like a stale slide count.
            page = Path(path).stem
            if page.isdigit() and int(page) <= 3:
                self._send_bytes(200, JPEG_BYTES, "image/jpeg")
            else:
                self._send_bytes(404, b"not found", "text/plain")
            return
        if path.startswith("/imageset/gap/"):
            # A hole in the middle: page 2 is absent but later pages exist.
            page = Path(path).stem
            if page == "2":
                self._send_bytes(404, b"not found", "text/plain")
            else:
                self._send_bytes(200, JPEG_BYTES, "image/jpeg")
            return
        self._send_bytes(404, b"not found", "text/plain")

    def _handle_resume_pdf(self) -> None:
        etag = '"fixture-pdf-v1"'
        range_header = self.headers.get("Range")
        if_range = self.headers.get("If-Range")
        if range_header and if_range == etag:
            start_text = range_header.removeprefix("bytes=").split("-", 1)[0]
            if start_text.isdigit():
                start = int(start_text)
                if start >= len(RESUME_PDF_BYTES):
                    self._send_bytes(
                        416,
                        b"",
                        "application/pdf",
                        {
                            "Content-Range": f"bytes */{len(RESUME_PDF_BYTES)}",
                            "ETag": etag,
                        },
                    )
                    return
                body = RESUME_PDF_BYTES[start:]
                self._send_bytes(
                    206,
                    body,
                    "application/pdf",
                    {
                        "Content-Range": (
                            f"bytes {start}-{len(RESUME_PDF_BYTES) - 1}/{len(RESUME_PDF_BYTES)}"
                        ),
                        "Accept-Ranges": "bytes",
                        "ETag": etag,
                        "Content-Disposition": 'attachment; filename="resumed.pdf"',
                    },
                )
                return

        if self.state.counts["/resume/pdf"] == 1:
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(RESUME_PDF_BYTES)))
            self.send_header("Content-Disposition", 'attachment; filename="resumed.pdf"')
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("ETag", etag)
            self.end_headers()
            halfway = max(1, len(RESUME_PDF_BYTES) // 2)
            try:
                self.wfile.write(RESUME_PDF_BYTES[:halfway])
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            finally:
                self.connection.close()
                self.close_connection = True
            return

        self._send_document(
            RESUME_PDF_BYTES,
            "application/pdf",
            'attachment; filename="resumed.pdf"',
            {"Accept-Ranges": "bytes", "ETag": etag},
        )

    def do_HEAD(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        if path.startswith("/imageset/truncated/"):
            page = Path(path).stem
            status = 200 if page.isdigit() and int(page) <= 3 else 404
            self._send_bytes(status, b"", "image/jpeg")
            return
        if path.startswith("/imageset/gap/"):
            page = Path(path).stem
            status = 404 if page == "2" else 200
            self._send_bytes(status, b"", "image/jpeg")
            return
        self._send_bytes(404, b"", "text/plain")

    def _send_document(
        self,
        body: bytes,
        content_type: str,
        disposition: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        combined = {"Content-Disposition": disposition, **(headers or {})}
        self._send_bytes(200, body, content_type, combined)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        del format, args


class FixtureHttpServer(ThreadingHTTPServer):
    def handle_error(self, request: object, client_address: object) -> None:
        del request, client_address


class FixtureServer:
    def __init__(self) -> None:
        self.state = FixtureState()
        self.server = FixtureHttpServer(("127.0.0.1", 0), FixtureHandler)
        self.server.fixture_state = self.state
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def __enter__(self) -> FixtureServer:
        self.thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
