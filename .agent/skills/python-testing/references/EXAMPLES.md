# Python Testing Examples

Practical examples demonstrating testing patterns from the skill.

## Basic pytest Test Structure

```python
"""Tests for the calculator module."""
from __future__ import annotations

import pytest

from mypackage.calculator import Calculator


class TestCalculator:
    """Tests for the Calculator class."""

    def test_add_positive_numbers(self) -> None:
        """Addition of positive numbers returns correct sum."""
        calc = Calculator()
        result = calc.add(2, 3)
        assert result == 5

    def test_add_negative_numbers(self) -> None:
        """Addition with negative numbers works correctly."""
        calc = Calculator()
        result = calc.add(-2, -3)
        assert result == -5

    def test_divide_by_zero_raises(self) -> None:
        """Division by zero raises ZeroDivisionError."""
        calc = Calculator()
        with pytest.raises(ZeroDivisionError):
            calc.divide(10, 0)

    @pytest.mark.parametrize(
        "a,b,expected",
        [
            pytest.param(2, 3, 6, id="positive"),
            pytest.param(-2, 3, -6, id="negative_first"),
            pytest.param(0, 5, 0, id="zero"),
        ],
    )
    def test_multiply(self, a: int, b: int, expected: int) -> None:
        """Multiplication returns correct product."""
        calc = Calculator()
        assert calc.multiply(a, b) == expected
```

## Fixtures Pattern

```python
"""Test fixtures for database tests."""
from __future__ import annotations

from collections.abc import Generator
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from mypackage.database import Database
    from mypackage.models import User


@pytest.fixture
def database() -> Generator[Database, None, None]:
    """Create a test database connection with cleanup."""
    db = Database.connect(":memory:")
    db.create_tables()
    yield db
    db.close()


@pytest.fixture
def sample_user(database: Database) -> User:
    """Create a sample user in the test database."""
    return database.users.create(
        name="Test User",
        email="test@example.com",
    )


@pytest.fixture
def make_user(database: Database):
    """Factory fixture for creating users with custom attributes."""
    created_users: list[User] = []

    def _make_user(name: str = "Test User", **kwargs) -> User:
        user = database.users.create(name=name, **kwargs)
        created_users.append(user)
        return user

    yield _make_user

    # Cleanup created users
    for user in created_users:
        database.users.delete(user.id)
```

## Mocking External Services

```python
"""Tests demonstrating mocking patterns."""
from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from mypackage.api_client import APIClient, APIError


class TestAPIClient:
    """Tests for the API client."""

    @patch("mypackage.api_client.requests.get")
    def test_fetch_data_success(self, mock_get: Mock) -> None:
        """Successful API call returns parsed data."""
        # Arrange
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok", "data": [1, 2, 3]}
        mock_get.return_value = mock_response

        # Act
        client = APIClient(base_url="https://api.example.com")
        result = client.fetch_data("/endpoint")

        # Assert
        assert result == {"status": "ok", "data": [1, 2, 3]}
        mock_get.assert_called_once_with(
            "https://api.example.com/endpoint",
            timeout=30,
        )

    @patch("mypackage.api_client.requests.get")
    def test_fetch_data_timeout_raises(self, mock_get: Mock) -> None:
        """Timeout raises APIError."""
        from requests.exceptions import Timeout

        mock_get.side_effect = Timeout("Connection timed out")

        client = APIClient(base_url="https://api.example.com")

        with pytest.raises(APIError) as exc_info:
            client.fetch_data("/endpoint")

        assert "Connection timed out" in str(exc_info.value)

    @patch("mypackage.api_client.requests.get")
    def test_fetch_data_retries_on_failure(self, mock_get: Mock) -> None:
        """Client retries on transient failures."""
        # First two calls fail, third succeeds
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "success"}

        mock_get.side_effect = [
            ConnectionError("Network error"),
            ConnectionError("Network error"),
            mock_response,
        ]

        client = APIClient(base_url="https://api.example.com", retries=3)
        result = client.fetch_data("/endpoint")

        assert result == {"data": "success"}
        assert mock_get.call_count == 3


class TestAsyncAPIClient:
    """Tests for async API client."""

    @pytest.mark.asyncio
    async def test_async_fetch_success(self) -> None:
        """Async fetch returns data correctly."""
        from mypackage.async_client import AsyncAPIClient

        mock_session = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json.return_value = {"result": "success"}
        mock_session.get.return_value.__aenter__.return_value = mock_response

        client = AsyncAPIClient(session=mock_session)
        result = await client.fetch("/data")

        assert result == {"result": "success"}
        mock_session.get.assert_awaited_once()
```

## Testing with patch.object and autospec

```python
"""Examples of patch.object and autospec usage."""
from __future__ import annotations

from unittest.mock import patch, create_autospec

import pytest

from mypackage.email_service import EmailService


class TestEmailService:
    """Tests demonstrating autospec for safety."""

    def test_send_email_with_autospec(self) -> None:
        """Using autospec catches signature mismatches."""
        # Create mock that respects the real signature
        mock_smtp = create_autospec(EmailService._smtp_client)

        with patch.object(
            EmailService,
            "_smtp_client",
            mock_smtp,
        ):
            service = EmailService()
            service.send(
                to="user@example.com",
                subject="Test",
                body="Hello!",
            )

            # This assertion would fail if we called with wrong signature
            mock_smtp.send_message.assert_called_once()

    def test_autospec_prevents_typos(self) -> None:
        """Autospec raises AttributeError for non-existent methods."""
        mock_service = create_autospec(EmailService)

        # This would raise AttributeError - method doesn't exist
        with pytest.raises(AttributeError):
            mock_service.sennd("test")  # Typo!
```

## Property-Based Testing with Hypothesis

```python
"""Property-based testing examples."""
from __future__ import annotations

from hypothesis import given, assume, strategies as st, settings, example

from mypackage.encoding import encode, decode


class TestEncoding:
    """Property-based tests for encoding functions."""

    @given(st.text())
    def test_encode_decode_roundtrip(self, text: str) -> None:
        """Encoding and decoding returns original text."""
        encoded = encode(text)
        decoded = decode(encoded)
        assert decoded == text

    @given(st.text(min_size=1))
    def test_encode_increases_length(self, text: str) -> None:
        """Encoded text is never shorter than original."""
        encoded = encode(text)
        assert len(encoded) >= len(text)

    @given(st.integers(), st.integers())
    def test_division_property(self, a: int, b: int) -> None:
        """Division satisfies a = b * (a // b) + (a % b)."""
        assume(b != 0)  # Filter out division by zero
        assert a == b * (a // b) + (a % b)

    @given(st.lists(st.integers()))
    @example([])  # Always test empty list
    @example([1])  # Always test single element
    @example([1, 1, 1])  # Test duplicates
    def test_sort_idempotent(self, lst: list[int]) -> None:
        """Sorting twice gives same result as sorting once."""
        sorted_once = sorted(lst)
        sorted_twice = sorted(sorted_once)
        assert sorted_once == sorted_twice

    @given(st.lists(st.integers()))
    @settings(max_examples=500)  # More thorough testing
    def test_sort_preserves_elements(self, lst: list[int]) -> None:
        """Sorting preserves all elements."""
        from collections import Counter

        sorted_lst = sorted(lst)
        assert Counter(lst) == Counter(sorted_lst)


# Composite strategy for complex data
@st.composite
def ordered_pair(draw):
    """Strategy that generates (a, b) where a <= b."""
    a = draw(st.integers(min_value=-1000, max_value=1000))
    b = draw(st.integers(min_value=a, max_value=1000))
    return (a, b)


@given(ordered_pair())
def test_range_contains_endpoints(pair: tuple[int, int]) -> None:
    """Range from a to b contains both endpoints."""
    a, b = pair
    r = range(a, b + 1)
    assert a in r
    assert b in r
```

## Testing Time-Dependent Code

```python
"""Testing code that depends on time."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

from mypackage.scheduler import Scheduler


class TestScheduler:
    """Tests for time-dependent scheduler."""

    @patch("mypackage.scheduler.datetime")
    def test_is_business_hours_morning(self, mock_dt) -> None:
        """9 AM on weekday is business hours."""
        mock_dt.now.return_value = datetime(2025, 1, 6, 9, 0)  # Monday 9 AM
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)

        scheduler = Scheduler()
        assert scheduler.is_business_hours() is True

    @patch("mypackage.scheduler.datetime")
    def test_is_business_hours_weekend(self, mock_dt) -> None:
        """Saturday is not business hours."""
        mock_dt.now.return_value = datetime(2025, 1, 4, 10, 0)  # Saturday
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)

        scheduler = Scheduler()
        assert scheduler.is_business_hours() is False

    def test_schedule_task_with_freezegun(self) -> None:
        """Using freezegun for time control (if installed)."""
        try:
            from freezegun import freeze_time
        except ImportError:
            pytest.skip("freezegun not installed")

        with freeze_time("2025-01-06 14:30:00"):
            scheduler = Scheduler()
            task = scheduler.schedule_task("meeting", hours=2)
            assert task.scheduled_time == datetime(2025, 1, 6, 16, 30)
```

## File I/O Testing

```python
"""Testing file operations."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

from mypackage.config import load_config, save_config


class TestConfig:
    """Tests for config file operations."""

    def test_load_config_from_file(self, tmp_path: Path) -> None:
        """Load config from real temporary file."""
        config_file = tmp_path / "config.json"
        config_file.write_text('{"debug": true, "port": 8080}')

        config = load_config(config_file)

        assert config["debug"] is True
        assert config["port"] == 8080

    def test_load_config_with_mock_open(self) -> None:
        """Load config using mock_open."""
        mock_data = '{"debug": false, "port": 3000}'

        with patch("builtins.open", mock_open(read_data=mock_data)):
            config = load_config(Path("fake/path.json"))

        assert config["debug"] is False
        assert config["port"] == 3000

    def test_save_config(self, tmp_path: Path) -> None:
        """Save config writes correct JSON."""
        config_file = tmp_path / "output.json"
        config = {"name": "test", "value": 42}

        save_config(config, config_file)

        saved = config_file.read_text()
        assert '"name": "test"' in saved
        assert '"value": 42' in saved

    def test_load_config_file_not_found(self) -> None:
        """Missing config file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_config(Path("/nonexistent/config.json"))
```

## Snapshot Testing Pattern

```python
"""Snapshot testing for complex outputs."""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

from mypackage.report import generate_report


class TestReport:
    """Tests using snapshot assertions."""

    def test_report_format(self, snapshot: SnapshotAssertion) -> None:
        """Report output matches snapshot."""
        data = {
            "sales": [100, 200, 150],
            "period": "Q1 2025",
        }

        report = generate_report(data)

        assert report == snapshot

    def test_report_with_custom_snapshot_name(
        self,
        snapshot: SnapshotAssertion,
    ) -> None:
        """Use custom snapshot name for clarity."""
        report = generate_report({"empty": True})

        assert report == snapshot(name="empty_report")
```

## conftest.py Example

```python
"""Shared test fixtures and configuration."""
from __future__ import annotations

from collections.abc import Generator
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock, patch

import pytest

if TYPE_CHECKING:
    from mypackage.database import Database


# Enable async fixtures
pytest_plugins = ["pytest_asyncio"]


@pytest.fixture
def mock_env_vars() -> Generator[dict[str, str], None, None]:
    """Mock environment variables for testing."""
    env = {
        "API_KEY": "test-key-12345",
        "DEBUG": "true",
        "DATABASE_URL": "sqlite://:memory:",
    }
    with patch.dict("os.environ", env, clear=False):
        yield env


@pytest.fixture
def mock_api_client() -> Mock:
    """Create a mock API client with common defaults."""
    client = Mock()
    client.fetch.return_value = {"status": "ok"}
    client.is_connected.return_value = True
    return client


@pytest.fixture
def mock_async_client() -> AsyncMock:
    """Create an async mock client."""
    client = AsyncMock()
    client.fetch.return_value = {"status": "ok"}
    return client


@pytest.fixture(scope="session")
def database_url() -> str:
    """Database URL for test session."""
    return "sqlite://:memory:"


@pytest.fixture
def database(database_url: str) -> Generator[Database, None, None]:
    """Create test database with cleanup."""
    from mypackage.database import Database

    db = Database(database_url)
    db.create_all()
    yield db
    db.drop_all()


# Configure pytest
def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line("markers", "slow: marks tests as slow")
    config.addinivalue_line("markers", "integration: integration tests")
```
