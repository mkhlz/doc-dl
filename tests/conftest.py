from __future__ import annotations

import pytest

from tests.fixture_server import FixtureServer


@pytest.fixture
def fixture_server() -> FixtureServer:
    with FixtureServer() as server:
        yield server
