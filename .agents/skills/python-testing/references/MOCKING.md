# unittest.mock Reference

Comprehensive guide to mocking in Python tests.

## Mock Objects

```python
from unittest.mock import Mock, MagicMock

# Basic Mock
mock = Mock()
mock.method.return_value = 42
assert mock.method() == 42

# MagicMock includes magic methods
magic = MagicMock()
magic.__len__.return_value = 5
assert len(magic) == 5

# Configure mock
mock = Mock(
    return_value=10,
    side_effect=None,
    name="my_mock",
)
```

## return_value vs side_effect

```python
from unittest.mock import Mock

# return_value: Always returns the same value
mock = Mock(return_value=42)
assert mock() == 42
assert mock("any", "args") == 42

# side_effect: Dynamic behavior
# 1. Raise exception
mock = Mock(side_effect=ValueError("invalid"))
with pytest.raises(ValueError):
    mock()

# 2. Return different values on successive calls
mock = Mock(side_effect=[1, 2, 3])
assert mock() == 1
assert mock() == 2
assert mock() == 3

# 3. Call a function
def dynamic_response(arg: int) -> int:
    return arg * 2

mock = Mock(side_effect=dynamic_response)
assert mock(5) == 10
```

## Assertions on Mocks

```python
from unittest.mock import Mock, call

mock = Mock()
mock(1, 2, key="value")
mock("another", "call")

# Was it called?
mock.assert_called()
mock.assert_called_once()  # Fails - called twice

# How was it called?
mock.assert_called_with("another", "call")  # Last call
mock.assert_any_call(1, 2, key="value")  # Any call

# Call history
assert mock.call_count == 2
assert mock.call_args == call("another", "call")
assert mock.call_args_list == [
    call(1, 2, key="value"),
    call("another", "call"),
]

# Assert specific call sequence
mock.assert_has_calls([
    call(1, 2, key="value"),
    call("another", "call"),
])

# Assert never called
other_mock = Mock()
other_mock.assert_not_called()
```

## patch() Decorator and Context Manager

```python
from unittest.mock import patch, MagicMock

# As decorator
@patch("module.ClassName")
def test_with_mock(mock_class: MagicMock) -> None:
    mock_class.return_value.method.return_value = 42
    result = function_under_test()
    assert result == 42

# As context manager
def test_with_context_manager() -> None:
    with patch("module.ClassName") as mock_class:
        mock_class.return_value.method.return_value = 42
        result = function_under_test()
        assert result == 42

# Stacking decorators (bottom-up order)
@patch("module.thing_a")
@patch("module.thing_b")
def test_stacked(mock_b: Mock, mock_a: Mock) -> None:
    # Note: Arguments are in reverse order of decorators
    ...
```

## Where to Patch (Critical!)

**Patch where the object is looked up, not where it's defined:**

```python
# mymodule.py
from datetime import datetime

def get_current_time():
    return datetime.now()

# test_mymodule.py
# CORRECT: Patch where it's used (looked up)
@patch("mymodule.datetime")
def test_get_current_time(mock_datetime: Mock) -> None:
    mock_datetime.now.return_value = datetime(2025, 1, 1)
    assert get_current_time() == datetime(2025, 1, 1)

# WRONG: Patching the source module
@patch("datetime.datetime")  # Won't work!
def test_wrong(mock_datetime: Mock) -> None:
    ...
```

## patch.object() for Specific Attributes

```python
from unittest.mock import patch

class MyClass:
    def method(self) -> int:
        return 42

# Patch specific method
@patch.object(MyClass, "method", return_value=100)
def test_method(mock_method: Mock) -> None:
    obj = MyClass()
    assert obj.method() == 100

# As context manager
def test_context() -> None:
    with patch.object(MyClass, "method", return_value=100):
        obj = MyClass()
        assert obj.method() == 100
```

## patch.dict() for Dictionaries

```python
from unittest.mock import patch
import os

@patch.dict(os.environ, {"API_KEY": "test-key"})
def test_with_env_var() -> None:
    assert os.environ["API_KEY"] == "test-key"

# Clear existing + add new
@patch.dict(os.environ, {"NEW_VAR": "value"}, clear=True)
def test_clean_env() -> None:
    assert "PATH" not in os.environ
    assert os.environ["NEW_VAR"] == "value"
```

## Autospec for Safety

```python
from unittest.mock import create_autospec, patch

class APIClient:
    def fetch(self, url: str, timeout: int = 30) -> dict:
        ...

# create_autospec enforces correct signature
mock_client = create_autospec(APIClient)
mock_client.fetch("http://example.com")  # OK
mock_client.fetch()  # TypeError: missing required argument

# With patch
@patch("module.APIClient", autospec=True)
def test_api(mock_client_class: Mock) -> None:
    instance = mock_client_class.return_value
    instance.fetch.return_value = {"data": "value"}
    ...
```

## AsyncMock for Async Code

```python
from unittest.mock import AsyncMock, patch
import pytest

async def fetch_data(client):
    return await client.get("/data")

@pytest.mark.asyncio
async def test_async_code() -> None:
    mock_client = AsyncMock()
    mock_client.get.return_value = {"result": "success"}
    
    result = await fetch_data(mock_client)
    
    assert result == {"result": "success"}
    mock_client.get.assert_awaited_once_with("/data")

# Async assertions
mock_client.get.assert_awaited()
mock_client.get.assert_awaited_once()
mock_client.get.assert_awaited_with("/data")
mock_client.get.assert_awaited_once_with("/data")
```

## mock_open for File Operations

```python
from unittest.mock import mock_open, patch

def read_config(path: str) -> dict:
    with open(path) as f:
        return json.load(f)

def test_read_config() -> None:
    mock_data = '{"key": "value"}'
    with patch("builtins.open", mock_open(read_data=mock_data)):
        result = read_config("config.json")
        assert result == {"key": "value"}
```

## ANY Matcher

```python
from unittest.mock import ANY, Mock

mock = Mock()
mock("test", timestamp=12345)

# Match any value for specific arguments
mock.assert_called_with("test", timestamp=ANY)

# Use in call_args comparison
assert mock.call_args == (("test",), {"timestamp": ANY})
```

## Quick Reference Table

| Method | Purpose |
|--------|---------|
| `Mock()` | Basic mock object |
| `MagicMock()` | Mock with magic methods |
| `AsyncMock()` | Async-aware mock |
| `patch()` | Replace object temporarily |
| `patch.object()` | Patch specific attribute |
| `patch.dict()` | Patch dictionary |
| `create_autospec()` | Mock respecting signature |
| `mock_open()` | Mock file operations |
| `ANY` | Match any value |

| Assertion | Checks |
|-----------|--------|
| `assert_called()` | Called at least once |
| `assert_called_once()` | Called exactly once |
| `assert_called_with(*args)` | Last call arguments |
| `assert_called_once_with(*args)` | Single call with args |
| `assert_any_call(*args)` | Any call had these args |
| `assert_has_calls([calls])` | Calls in order |
| `assert_not_called()` | Never called |
| `assert_awaited*()` | Async versions |
