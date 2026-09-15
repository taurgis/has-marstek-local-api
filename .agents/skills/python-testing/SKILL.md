---
name: python-testing
description: Python testing best practices covering pytest, unittest.mock, property-based testing with Hypothesis, fixtures, and mocking patterns
---

# Python Testing Best Practices

Expert-level guidance for writing effective, maintainable Python tests.

## The Zen of Testing

```
Tests should be fast and deterministic.
Each test should verify one thing.
Tests are documentation—make them readable.
Mock at boundaries, not implementation details.
Prefer integration over unit when boundaries are unclear.
Failing tests should tell you what broke.
Test behavior, not implementation.
```

## Test Organization

### Directory Structure

```
tests/
├── conftest.py          # Shared fixtures, plugins
├── test_module.py       # Tests for module.py
├── test_feature/        # Feature-specific tests
│   ├── conftest.py      # Feature-specific fixtures
│   └── test_component.py
└── fixtures/            # Test data files
    └── sample_data.json
```

### Naming Conventions

| Element | Convention | Example |
|---------|------------|---------|
| Test files | `test_*.py` or `*_test.py` | `test_calculator.py` |
| Test classes | `Test*` (optional) | `TestCalculator` |
| Test functions | `test_*` | `test_add_positive_numbers` |
| Fixtures | Descriptive nouns | `mock_database`, `sample_user` |

Use descriptive names: `test_<what>_<condition>_<expected>`

```python
def test_divide_by_zero_raises_exception() -> None: ...
def test_login_with_invalid_password_returns_401() -> None: ...
```

## pytest Quick Reference

### AAA Pattern

```python
def test_user_creation() -> None:
    # Arrange
    email = "test@example.com"
    
    # Act
    user = User.create(email=email)
    
    # Assert
    assert user.email == email
```

### Key Assertions

```python
assert value == expected
assert value is None / is not None
assert value in collection
assert value == pytest.approx(expected, rel=1e-3)  # floats

with pytest.raises(ValueError) as exc_info:
    function_that_raises()
```

### Parametrized Tests

```python
@pytest.mark.parametrize("input_val,expected", [
    (1, 2), (2, 4), (-1, -2),
])
def test_double(input_val: int, expected: int) -> None:
    assert double(input_val) == expected
```

### Common Markers

```python
@pytest.mark.slow            # Custom marker
@pytest.mark.skip(reason="") # Always skip
@pytest.mark.skipif(cond)    # Conditional skip
@pytest.mark.xfail           # Expected failure
@pytest.mark.asyncio         # Async test
```

**See:** [references/PYTEST.md](references/PYTEST.md) for fixtures, scopes, factories, teardown patterns.

## unittest.mock Essentials

### Core Concepts

```python
from unittest.mock import Mock, MagicMock, patch, AsyncMock

mock = Mock(return_value=42)
mock = Mock(side_effect=[1, 2, 3])  # Sequential returns
mock = Mock(side_effect=ValueError)  # Raise exception
```

### Where to Patch (Critical!)

**Patch where the object is looked up, not where it's defined:**

```python
# mymodule.py
from datetime import datetime
def get_time(): return datetime.now()

# test_mymodule.py
@patch("mymodule.datetime")  # ✓ Correct: where used
def test_time(mock_dt): ...

@patch("datetime.datetime")  # ✗ Wrong: where defined
def test_wrong(mock_dt): ...
```

### Key Assertions

```python
mock.assert_called()
mock.assert_called_once()
mock.assert_called_with(*args, **kwargs)
mock.assert_called_once_with(*args, **kwargs)
mock.assert_not_called()
mock.assert_has_calls([call(...), call(...)])
```

### Patch Variants

```python
@patch("module.Class")              # Replace object
@patch.object(Class, "method")      # Patch attribute
@patch.dict(os.environ, {"K": "V"}) # Patch dict
@patch("module.Class", autospec=True)  # Enforce signature
```

**See:** [references/MOCKING.md](references/MOCKING.md) for AsyncMock, mock_open, ANY matcher, autospec details.

## Hypothesis (Property-Based Testing)

Generate test cases to find edge cases automatically:

```python
from hypothesis import given, strategies as st

@given(st.integers())
def test_abs_non_negative(n: int) -> None:
    assert abs(n) >= 0

@given(st.lists(st.integers()))
def test_sort_length(lst: list[int]) -> None:
    assert len(sorted(lst)) == len(lst)
```

**See:** [references/HYPOTHESIS.md](references/HYPOTHESIS.md) for strategies, assume(), composite strategies.

## Async Testing

```python
@pytest.mark.asyncio
async def test_async_function() -> None:
    result = await async_fetch()
    assert result == expected

# AsyncMock for mocking
mock_client = AsyncMock()
mock_client.get.return_value = {"data": "value"}
mock_client.get.assert_awaited_once_with("/path")
```

**See:** [references/ASYNC_AND_COVERAGE.md](references/ASYNC_AND_COVERAGE.md) for fixtures, timeouts, coverage config.

## Coverage

```bash
pytest --cov=mypackage --cov-fail-under=95 tests/
pytest --cov=mypackage --cov-report=html tests/
```

## Quick Checklist

Before committing tests:

- [ ] AAA pattern (Arrange-Act-Assert)
- [ ] Descriptive test names
- [ ] One assertion concept per test
- [ ] Mocks patch at lookup location
- [ ] No shared mutable state
- [ ] Fixtures handle teardown
- [ ] Async tests use `@pytest.mark.asyncio`
- [ ] Coverage meets threshold
- [ ] Deterministic (no flaky tests)
- [ ] `autospec=True` for signature safety

**See:** [references/ANTI_PATTERNS.md](references/ANTI_PATTERNS.md) for common mistakes to avoid.

## Reference Files

| Topic | File |
|-------|------|
| pytest details | [references/PYTEST.md](references/PYTEST.md) |
| Mocking patterns | [references/MOCKING.md](references/MOCKING.md) |
| Property-based testing | [references/HYPOTHESIS.md](references/HYPOTHESIS.md) |
| Async & coverage | [references/ASYNC_AND_COVERAGE.md](references/ASYNC_AND_COVERAGE.md) |
| Anti-patterns | [references/ANTI_PATTERNS.md](references/ANTI_PATTERNS.md) |
| Full examples | [references/EXAMPLES.md](references/EXAMPLES.md) |
