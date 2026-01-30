# Testing Anti-Patterns to Avoid

Common mistakes that lead to flaky, slow, or useless tests.

## 1. Testing Implementation, Not Behavior

```python
# BAD: Tests internal state
def test_bad() -> None:
    calc = Calculator()
    calc.add(5)
    assert calc._internal_state == 5  # Don't test private!

# GOOD: Tests behavior
def test_good() -> None:
    calc = Calculator()
    calc.add(5)
    assert calc.get_result() == 5
```

## 2. Over-Mocking

```python
# BAD: Mocking everything
def test_over_mocked() -> None:
    with patch("module.func1"), \
         patch("module.func2"), \
         patch("module.func3"), \
         patch("module.func4"):
        result = function_under_test()
        # What are we even testing?

# GOOD: Mock only external dependencies
def test_focused() -> None:
    with patch("module.external_api"):
        result = function_under_test()
        assert result == expected
```

## 3. Non-Deterministic Tests

```python
# BAD: Uses current time
def test_flaky() -> None:
    result = get_greeting()
    assert "Good morning" in result  # Fails at night!

# GOOD: Control time
@patch("module.datetime")
def test_stable(mock_dt: Mock) -> None:
    mock_dt.now.return_value = datetime(2025, 1, 1, 9, 0)  # Morning
    result = get_greeting()
    assert "Good morning" in result
```

## 4. Tests Without Assertions

```python
# BAD: No assertions
def test_no_assertion() -> None:
    result = function()
    # Missing assertion!

# GOOD: Always assert
def test_with_assertion() -> None:
    result = function()
    assert result == expected
```

## 5. Shared Mutable State

```python
# BAD: Tests affect each other
test_data = []  # Module-level mutable!

def test_first() -> None:
    test_data.append(1)
    assert len(test_data) == 1

def test_second() -> None:
    assert len(test_data) == 0  # Fails!

# GOOD: Use fixtures
@pytest.fixture
def test_data() -> list:
    return []
```

## 6. Patching in the Wrong Location

```python
# BAD: Patching where defined
@patch("datetime.datetime")  # Won't work if module imports datetime
def test_wrong() -> None:
    ...

# GOOD: Patch where looked up
@patch("mymodule.datetime")  # Patch in the module that uses it
def test_correct() -> None:
    ...
```

## 7. Testing Too Much in One Test

```python
# BAD: Multiple concerns
def test_everything() -> None:
    user = create_user()
    assert user.id is not None
    user.update_name("New Name")
    assert user.name == "New Name"
    user.delete()
    assert user.is_deleted

# GOOD: One concept per test
def test_create_user_assigns_id() -> None:
    user = create_user()
    assert user.id is not None

def test_update_name_changes_name() -> None:
    user = create_user()
    user.update_name("New Name")
    assert user.name == "New Name"
```

## 8. Slow Tests Without Markers

```python
# BAD: Slow test mixed with fast ones
def test_integration_with_real_database() -> None:
    # Takes 30 seconds...

# GOOD: Mark slow tests
@pytest.mark.slow
@pytest.mark.integration
def test_integration_with_real_database() -> None:
    # Can be skipped with: pytest -m "not slow"
    ...
```

## 9. Ignoring Test Isolation

```python
# BAD: Test depends on external state
def test_config() -> None:
    result = load_config()  # Reads from ~/.config/app.json
    assert result["key"] == "value"  # Fails on different machines!

# GOOD: Control all inputs
def test_config(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text('{"key": "value"}')
    with patch("module.CONFIG_PATH", config_file):
        result = load_config()
        assert result["key"] == "value"
```

## 10. Not Testing Error Cases

```python
# BAD: Only happy path
def test_divide() -> None:
    assert divide(10, 2) == 5

# GOOD: Include error cases
def test_divide_success() -> None:
    assert divide(10, 2) == 5

def test_divide_by_zero_raises() -> None:
    with pytest.raises(ZeroDivisionError):
        divide(10, 0)
```

## Quick Checklist

Before merging tests, verify:

- [ ] No tests depend on each other's state
- [ ] No tests use real time/dates without mocking
- [ ] No tests access real network/filesystem without mocking
- [ ] Every test has at least one assertion
- [ ] Slow tests are marked appropriately
- [ ] Mocks patch at lookup location, not definition
- [ ] No testing of private/internal implementation
- [ ] Error cases are covered, not just happy paths
