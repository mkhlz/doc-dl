from __future__ import annotations

import pytest

from doc_dl.config import StatePaths
from doc_dl.errors import DocDlError
from doc_dl.providers.registry import ProviderRegistry
from doc_dl.providers.scribd import ScribdProvider


def test_scribd_provider_normalizes_both_url_styles() -> None:
    provider = ScribdProvider()
    assert provider.normalize("https://www.scribd.com/doc/12345/a-title") == (
        "https://www.scribd.com/document/12345"
    )
    assert provider.browser_url("https://www.scribd.com/document/12345/a-title") == (
        "https://www.scribd.com/embeds/12345/content"
    )


def test_registry_prefers_scribd_over_generic() -> None:
    provider = ProviderRegistry().select("https://www.scribd.com/document/12345/a-title")
    assert provider.name == "scribd"


def test_profile_name_cannot_escape_state_root(tmp_path) -> None:
    paths = StatePaths(tmp_path)
    with pytest.raises(DocDlError) as raised:
        paths.profile("scribd", "../../escape")
    assert raised.value.identifier == "invalid_arguments"
