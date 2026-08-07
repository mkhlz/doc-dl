from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    """How a failure should read, which is not the same as how bad it is.

    A private link and a corrupt PDF both stop the download, but only one is
    something the person can act on in the next few seconds, so they are shown
    differently.
    """

    ACTIONABLE = "actionable"
    """The person can fix this: grant access, pass a flag, free the name."""

    FAILED = "failed"
    """The attempt did not work, usually because of the site or the network."""

    MISTAKE = "mistake"
    """The command or URL was not something doc-dl can act on."""

    BUG = "bug"
    """doc-dl itself is at fault and should be told about it."""


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

SEVERITIES: dict[str, Severity] = {
    "invalid_arguments": Severity.MISTAKE,
    "unsupported_url": Severity.MISTAKE,
    "authentication_required": Severity.ACTIONABLE,
    "access_denied": Severity.ACTIONABLE,
    "interactive_challenge": Severity.ACTIONABLE,
    "output_exists": Severity.ACTIONABLE,
    "browser_unavailable": Severity.ACTIONABLE,
    "network_failure": Severity.FAILED,
    "retry_exhausted": Severity.FAILED,
    "resume_mismatch": Severity.FAILED,
    "candidate_not_found": Severity.FAILED,
    "extraction_failed": Severity.FAILED,
    "render_incomplete": Severity.FAILED,
    "verification_failed": Severity.FAILED,
    "unexpected_content": Severity.FAILED,
    "corrupt_document": Severity.FAILED,
    "browser_failed": Severity.FAILED,
    "operation_timeout": Severity.FAILED,
    "filesystem_failure": Severity.FAILED,
    "internal_error": Severity.BUG,
}

REMEDIES: dict[str, str] = {
    "invalid_arguments": "Run 'doc-dl' with no arguments to see the accepted forms.",
    "unsupported_url": "Give the page's own address, or the direct link to the file.",
    "interactive_challenge": (
        "The site asked a human to prove they are one. Open the link in your "
        "browser, clear the check, then try again."
    ),
    "output_exists": "Pass --overwrite to replace it, or --output to save elsewhere.",
    "browser_unavailable": "Run 'doc-dl install-browser' to fetch the browser runtime.",
    "network_failure": "Check your connection and try again.",
    "retry_exhausted": "The site kept refusing. Wait a little and try again.",
    "resume_mismatch": "The file changed while resuming. Retry with --no-resume.",
    "candidate_not_found": "Nothing on that page looked like a downloadable document.",
    "operation_timeout": "Raise the limit with --timeout 10m for a long document.",
    "corrupt_document": "The site sent a damaged file. Retrying sometimes helps.",
    "unexpected_content": "The site returned a page instead of a file.",
    "filesystem_failure": "Check the folder exists and that you can write to it.",
    "internal_error": "This one is on us. Report it at github.com/mkhlz/doc-dl/issues",
}


def severity_of(identifier: str) -> Severity:
    return SEVERITIES.get(identifier, Severity.FAILED)


def remedy_for(identifier: str) -> str | None:
    return REMEDIES.get(identifier)


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
