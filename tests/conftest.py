from datetime import UTC, datetime
from pathlib import Path

import pytest

# Fixed clock for every test that needs "now".
NOW = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def now() -> datetime:
    return NOW
