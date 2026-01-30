# Async Testing & Coverage Reference

## Testing Async Code

### pytest-asyncio

```python
import pytest

@pytest.mark.asyncio
async def test_async_function() -> None:
    result = await async_fetch_data()
    assert result == expected

# Fixture for async resources
@pytest.fixture
async def async_client() -> AsyncGenerator[Client, None]:
    client = await Client.connect()
    yield client
    await client.disconnect()

@pytest.mark.asyncio
async def test_with_client(async_client: Client) -> None:
    result = await async_client.fetch("/data")
    assert result is not None
```

### Testing Timeouts and Delays

```python
import pytest
import asyncio

@pytest.mark.asyncio
async def test_timeout() -> None:
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            slow_operation(),
            timeout=0.1
        )
```

### AsyncMock Pattern

```python
from unittest.mock import AsyncMock

@pytest.mark.asyncio
async def test_async_dependency() -> None:
    mock_service = AsyncMock()
    mock_service.fetch.return_value = {"data": "value"}
    
    result = await code_under_test(mock_service)
    
    mock_service.fetch.assert_awaited_once()
```

## Test Coverage

### Running with Coverage

```bash
# Basic coverage
pytest --cov=mypackage tests/

# With HTML report
pytest --cov=mypackage --cov-report=html tests/

# Fail if coverage below threshold
pytest --cov=mypackage --cov-fail-under=95 tests/

# Multiple reports
pytest --cov=mypackage \
       --cov-report=term-missing \
       --cov-report=xml \
       tests/
```

### Coverage Configuration (pyproject.toml)

```toml
[tool.coverage.run]
source = ["mypackage"]
branch = true
omit = ["*/tests/*", "*/__main__.py"]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "if TYPE_CHECKING:",
    "raise NotImplementedError",
    "@abstractmethod",
]
fail_under = 95
```

### Common Exclusion Patterns

```python
# Exclude from coverage with pragma
if TYPE_CHECKING:  # pragma: no cover
    from typing import Protocol

# Exclude abstract methods
@abstractmethod
def must_implement(self) -> None:  # pragma: no cover
    ...

# Exclude defensive code that's hard to test
if sys.platform == "win32":  # pragma: no cover
    # Windows-specific code
    pass
```

## Coverage Best Practices

1. **Set a threshold** - Use `--cov-fail-under=95` in CI
2. **Branch coverage** - Enable `branch = true` to catch untested branches
3. **Focus on meaningful coverage** - Don't test trivial getters/setters just for numbers
4. **Exclude appropriately** - TYPE_CHECKING blocks, abstract methods, platform-specific code
5. **Review uncovered lines** - Missing coverage often indicates missing test cases
