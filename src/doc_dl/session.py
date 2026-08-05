from __future__ import annotations

import shutil
from pathlib import Path

from doc_dl.browser import BrowserExtractor
from doc_dl.config import StatePaths
from doc_dl.errors import DocDlError
from doc_dl.events import EventSink
from doc_dl.providers.base import Provider


class SessionManager:
    def __init__(self, sink: EventSink, state: StatePaths | None = None) -> None:
        self.sink = sink
        self.state = state or StatePaths.discover()

    def login(self, provider: Provider, profile: str) -> Path:
        BrowserExtractor(self.sink, self.state).login(provider, profile)
        path = self.state.profile(provider.name, profile)
        self.sink.emit(
            "complete",
            message=f"Saved isolated {provider.name} profile '{profile}'",
            path=str(path),
            provider=provider.name,
            profile=profile,
        )
        return path

    def logout(self, provider: Provider, profile: str, *, confirmed: bool = False) -> bool:
        path = self.state.profile(provider.name, profile)
        if not path.exists():
            return False
        if not confirmed:
            answer = input(
                f"Delete the isolated {provider.name} profile '{profile}' at {path}? [y/N] "
            )
            if answer.strip().casefold() not in {"y", "yes"}:
                return False
        profiles_root = (self.state.root / "profiles").resolve()
        resolved = path.resolve()
        if profiles_root not in resolved.parents:
            raise DocDlError(
                "filesystem_failure",
                "Refusing to delete a profile outside the doc-dl state directory",
            )
        try:
            shutil.rmtree(resolved)
        except OSError as exc:
            raise DocDlError(
                "filesystem_failure",
                "The isolated profile could not be deleted",
                detail=str(exc),
            ) from exc
        return True
