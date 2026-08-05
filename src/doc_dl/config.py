from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from doc_dl.errors import DocDlError

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def state_root() -> Path:
    override = os.environ.get("DOC_DL_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()

    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return (Path(base) / "doc-dl").resolve()

    xdg_state = os.environ.get("XDG_STATE_HOME")
    if xdg_state:
        return (Path(xdg_state) / "doc-dl").expanduser().resolve()
    return (Path.home() / ".local" / "state" / "doc-dl").resolve()


def validate_state_name(value: str, label: str) -> str:
    if not _SAFE_NAME.fullmatch(value):
        raise DocDlError(
            "invalid_arguments",
            f"Invalid {label} name: {value!r}",
            detail="Use letters, numbers, dots, underscores, or hyphens; maximum 64 characters.",
        )
    return value


@dataclass(frozen=True, slots=True)
class StatePaths:
    root: Path

    @classmethod
    def discover(cls) -> StatePaths:
        return cls(state_root())

    def profile(self, provider: str, profile: str) -> Path:
        safe_provider = validate_state_name(provider, "provider")
        safe_profile = validate_state_name(profile, "profile")
        profiles_root = (self.root / "profiles").resolve()
        result = (profiles_root / safe_provider / safe_profile).resolve()
        if profiles_root != result and profiles_root not in result.parents:
            raise DocDlError("invalid_arguments", "Profile path escaped the state directory")
        return result

    def cache(self) -> Path:
        return (self.root / "cache").resolve()
