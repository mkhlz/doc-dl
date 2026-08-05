from __future__ import annotations

from doc_dl.providers.base import Provider


class GenericProvider(Provider):
    name = "generic"
    supports_authentication = False
    supports_render = True

    def match(self, url: str) -> int:
        del url
        return 1
