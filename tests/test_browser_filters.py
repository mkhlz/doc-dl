from __future__ import annotations

import pytest

from doc_dl.browser import _is_ad_tracker_host


@pytest.mark.parametrize(
    "hostname",
    [
        "securepubads.g.doubleclick.net",
        "pagead2.googlesyndication.com",
        "www.google-analytics.com",
        "cdn.taboola.com",
    ],
)
def test_known_ad_tracker_hosts_are_filtered(hostname: str) -> None:
    assert _is_ad_tracker_host(hostname) is True


@pytest.mark.parametrize(
    "hostname",
    [
        "www.mid-day.com",
        "en.wikipedia.org",
        "example.com",
        "notdoubleclick.net.evil.example",
    ],
)
def test_ordinary_hosts_are_not_filtered(hostname: str) -> None:
    assert _is_ad_tracker_host(hostname) is False
