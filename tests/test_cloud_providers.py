from __future__ import annotations

from doc_dl.providers.dropbox import DropboxProvider
from doc_dl.providers.onedrive import OneDriveProvider
from doc_dl.providers.registry import ProviderRegistry


def test_dropbox_forces_the_direct_download_flag() -> None:
    provider = DropboxProvider()
    assert (
        provider.normalize("https://www.dropbox.com/s/abc123/report.pdf?dl=0")
        == "https://www.dropbox.com/s/abc123/report.pdf?dl=1"
    )


def test_dropbox_preserves_the_rlkey_newer_links_require() -> None:
    provider = DropboxProvider()
    normalized = provider.normalize(
        "https://www.dropbox.com/scl/fi/xyz/report.pdf?rlkey=secretkey&dl=0"
    )
    assert "rlkey=secretkey" in normalized
    assert normalized.endswith("dl=1")


def test_dropbox_adds_the_flag_when_absent() -> None:
    provider = DropboxProvider()
    assert (
        provider.normalize("https://www.dropbox.com/s/abc123/report.pdf")
        == "https://www.dropbox.com/s/abc123/report.pdf?dl=1"
    )


def test_dropbox_leaves_content_hosts_alone() -> None:
    provider = DropboxProvider()
    url = "https://dl.dropboxusercontent.com/s/abc123/report.pdf"
    assert provider.match(url) == 90
    assert provider.normalize(url) == url


def test_dropbox_ignores_other_sites() -> None:
    assert DropboxProvider().match("https://example.com/s/abc/report.pdf") == 0


def test_onedrive_leaves_modern_share_links_untouched() -> None:
    # Verified against a real link: personal OneDrive is now SharePoint-backed
    # and no rewrite reaches the file, so rewriting would only replace a real
    # page with a dead endpoint.
    provider = OneDriveProvider()
    url = "https://1drv.ms/b/c/8adc1807017f41c6/IQB9frsxoQIBQJtz?e=N8fXAd"
    assert provider.match(url) == 100
    assert provider.normalize(url) == url


def test_onedrive_legacy_authkey_link_asks_for_the_file() -> None:
    provider = OneDriveProvider()
    normalized = provider.normalize(
        "https://onedrive.live.com/?cid=ABC123&resid=ABC123%21456&authkey=key"
    )
    assert normalized.startswith("https://onedrive.live.com/download?")
    assert "download=1" in normalized
    assert "authkey=key" in normalized


def test_onedrive_does_not_duplicate_the_download_flag() -> None:
    provider = OneDriveProvider()
    normalized = provider.normalize(
        "https://onedrive.live.com/?resid=ABC%21456&authkey=key&download=1"
    )
    assert normalized.count("download=1") == 1


def test_onedrive_explains_how_to_get_a_gated_file() -> None:
    hint = OneDriveProvider().access_hint()
    assert hint and "Download button" in hint


def test_onedrive_ignores_business_and_sharepoint_hosts() -> None:
    # These use a different mechanism; rewriting them would produce a dead URL.
    provider = OneDriveProvider()
    url = "https://contoso-my.sharepoint.com/:b:/g/personal/user/AbC123"
    assert provider.match(url) == 0
    assert provider.normalize(url) == url


def test_registry_selects_the_cloud_providers() -> None:
    registry = ProviderRegistry()
    assert registry.select("https://www.dropbox.com/s/abc/report.pdf?dl=0").name == "dropbox"
    assert registry.select("https://1drv.ms/b/s!AbCdEf").name == "onedrive"
