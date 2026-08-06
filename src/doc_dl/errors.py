from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ErrorSpec:
    identifier: str
    exit_code: int


ERRORS: dict[str, ErrorSpec] = {
    "invalid_arguments": ErrorSpec("invalid_arguments", 2),
    "unsupported_url": ErrorSpec("unsupported_url", 10),
    "authentication_required": ErrorSpec("authentication_required", 11),
    "access_denied": ErrorSpec("access_denied", 12),
    "interactive_challenge": ErrorSpec("interactive_challenge", 13),
    "network_failure": ErrorSpec("network_failure", 20),
    "retry_exhausted": ErrorSpec("retry_exhausted", 21),
    "resume_mismatch": ErrorSpec("resume_mismatch", 22),
    "candidate_not_found": ErrorSpec("candidate_not_found", 30),
    "extraction_failed": ErrorSpec("extraction_failed", 31),
    "render_incomplete": ErrorSpec("render_incomplete", 32),
    "verification_failed": ErrorSpec("verification_failed", 40),
    "unexpected_content": ErrorSpec("unexpected_content", 41),
    "corrupt_document": ErrorSpec("corrupt_document", 42),
    "browser_unavailable": ErrorSpec("browser_unavailable", 50),
    "browser_failed": ErrorSpec("browser_failed", 51),
    "operation_timeout": ErrorSpec("operation_timeout", 52),
    "output_exists": ErrorSpec("output_exists", 70),
    "filesystem_failure": ErrorSpec("filesystem_failure", 71),
    "internal_error": ErrorSpec("internal_error", 99),
}


class DocDlError(Exception):
    """An expected command failure with a stable identifier and exit code."""

    def __init__(
        self,
        identifier: str,
        message: str,
        *,
        detail: str | None = None,
        retryable: bool = False,
    ) -> None:
        if identifier not in ERRORS:
            raise ValueError(f"Unknown doc-dl error identifier: {identifier}")
        super().__init__(message)
        self.identifier = identifier
        self.message = message
        self.detail = detail
        self.retryable = retryable

    @property
    def exit_code(self) -> int:
        return ERRORS[self.identifier].exit_code

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "event": "error",
            "version": 1,
            "error": self.identifier,
            "message": self.message,
            "exit_code": self.exit_code,
            "retryable": self.retryable,
        }
        if self.detail:
            payload["detail"] = self.detail
        return payload
