# pytest Reference

Detailed pytest patterns and features.

## Basic Test Structure (AAA Pattern)

```python
def test_user_creation() -> None:
    # Arrange - Set up test data and conditions
    email = "test@example.com"
    name = "Test User"
    
    # Act - Perform the action being tested
    user = User.create(email=email, name=name)
    
    # Assert - Verify the results
    assert user.email == email
    assert user.name == name
    assert user.id is not None
```

## Assertions

```python
import pytest

# Basic assertions
assert value == expected
assert value != other
assert value is None
assert value is not None
assert value in collection
assert value not in collection

# Boolean assertions
assert condition  # Truthy
assert not condition  # Falsy

# Approximate comparisons (floats)
assert value == pytest.approx(expected, rel=1e-3)
assert value == pytest.approx(expected, abs=0.01)

# Exception assertions
with pytest.raises(ValueError) as exc_info:
    function_that_raises()
assert "expected message" in str(exc_info.value)

# Warning assertions
with pytest.warns(DeprecationWarning):
    deprecated_function()
```

## Parametrized Tests

```python
import pytest

@pytest.mark.parametrize("input_val,expected", [
    (1, 2),
    (2, 4),
    (3, 6),
    (-1, -2),
    (0, 0),
])
def test_double(input_val: int, expected: int) -> None:
    assert double(input_val) == expected

# Multiple parameters with IDs for clarity
@pytest.mark.parametrize(
    "a,b,expected",
    [
        pytest.param(2, 3, 5, id="positive"),
        pytest.param(-1, 1, 0, id="mixed"),
        pytest.param(0, 0, 0, id="zeros"),
    ],
)
def test_add(a: int, b: int, expected: int) -> None:
    assert add(a, b) == expected
```

## Markers

```python
import pytest

@pytest.mark.slow
def test_large_dataset() -> None:
    """Run with: pytest -m slow"""
    ...

@pytest.mark.skip(reason="Not implemented yet")
def test_future_feature() -> None:
    ...

@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Unix-only test"
)
def test_unix_permissions() -> None:
    ...

@pytest.mark.xfail(reason="Known bug #123")
def test_known_failure() -> None:
    ...

# Register markers in conftest.py or pyproject.toml
def pytest_configure(config):
    config.addinivalue_line("markers", "slow: marks tests as slow")
```

## Fixtures

### Basic Fixtures

```python
import pytest

@pytest.fixture
def sample_user() -> User:
    """Create a sample user for testing."""
    return User(name="Test User", email="test@example.com")

def test_user_greeting(sample_user: User) -> None:
    assert sample_user.greeting() == "Hello, Test User!"
```

### Fixture Scopes

```python
@pytest.fixture(scope="function")  # Default: new instance per test
def temp_file():
    ...

@pytest.fixture(scope="class")  # Shared within test class
def database_connection():
    ...

@pytest.fixture(scope="module")  # Shared within test module
def expensive_resource():
    ...

@pytest.fixture(scope="session")  # Shared across entire test session
def docker_container():
    ...
```

### Fixture Teardown

```python
# Using yield (preferred)
@pytest.fixture
def database() -> Generator[Database, None, None]:
    db = Database.connect()
    yield db
    db.disconnect()

# Using request.addfinalizer
@pytest.fixture
def temp_directory(request: pytest.FixtureRequest) -> Path:
    path = Path(tempfile.mkdtemp())
    request.addfinalizer(lambda: shutil.rmtree(path))
    return path
```

### Fixture Factories

```python
@pytest.fixture
def make_user() -> Callable[..., User]:
    """Factory fixture for creating users with custom attributes."""
    created_users: list[User] = []

    def _make_user(
        name: str = "Test User",
        email: str | None = None,
        **kwargs: Any,
    ) -> User:
        if email is None:
            email = f"{name.lower().replace(' ', '.')}@test.com"
        user = User(name=name, email=email, **kwargs)
        created_users.append(user)
        return user

    yield _make_user

    # Cleanup all created users
    for user in created_users:
        user.delete()
```

### autouse Fixtures

```python
@pytest.fixture(autouse=True)
def reset_environment() -> Generator[None, None, None]:
    """Automatically reset environment for each test."""
    original_env = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(original_env)
```
