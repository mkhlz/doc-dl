from __future__ import annotations

from collections.abc import Iterable

from doc_dl.errors import DocDlError
from doc_dl.providers.base import Provider
from doc_dl.providers.generic import GenericProvider
from doc_dl.providers.scribd import ScribdProvider
from doc_dl.providers.slideshare import SlideShareProvider


class ProviderRegistry:
    def __init__(self, providers: Iterable[Provider] | None = None) -> None:
        self._providers = list(
            providers or [ScribdProvider(), SlideShareProvider(), GenericProvider()]
        )

    def all(self) -> list[Provider]:
        return list(self._providers)

    def get(self, name: str) -> Provider:
        lowered = name.casefold()
        for provider in self._providers:
            if provider.name.casefold() == lowered:
                return provider
        raise DocDlError("unsupported_url", f"Unknown provider: {name}")

    def select(self, url: str, forced: str | None = None) -> Provider:
        if forced:
            provider = self.get(forced)
            if provider.match(url) <= 0 and provider.name != "generic":
                raise DocDlError(
                    "unsupported_url",
                    f"The URL does not match the forced provider {provider.name}",
                )
            return provider
        ranked = sorted(
            ((provider.match(url), provider) for provider in self._providers),
            key=lambda item: item[0],
            reverse=True,
        )
        if not ranked or ranked[0][0] <= 0:
            raise DocDlError("unsupported_url", "No provider supports this URL")
        return ranked[0][1]
