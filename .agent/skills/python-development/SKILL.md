---
name: python-development
description: Python best practices for code style, type hints, error handling, naming, documentation, and modern patterns based on PEP 8, PEP 257, PEP 20, and Google Python Style Guide
---

# Python Development Best Practices

Expert-level Python development guidance based on official sources: PEP 8, PEP 20 (Zen of Python), PEP 257, and Google Python Style Guide.

## The Zen of Python (PEP 20)

Core principles that guide all Python decisions:

```
Beautiful is better than ugly.
Explicit is better than implicit.
Simple is better than complex.
Flat is better than nested.
Readability counts.
Errors should never pass silently (unless explicitly silenced).
In the face of ambiguity, refuse the temptation to guess.
There should be one-- and preferably only one --obvious way to do it.
If the implementation is hard to explain, it's a bad idea.
```

## Code Style (PEP 8)

### Indentation & Line Length

| Rule | Standard |
|------|----------|
| Indentation | 4 spaces (never tabs) |
| Line length | 79 characters (72 for docstrings/comments) |
| Blank lines | 2 before top-level definitions, 1 between methods |

### Imports

```python
# Correct order: stdlib, third-party, local
import os
import sys

import requests

from myproject import utils
```

**Rules:**
- One import per line (except `from X import a, b, c`)
- Absolute imports preferred over relative
- Never use `from module import *`
- Group imports with blank lines between groups

### Whitespace

```python
# Correct
spam(ham[1], {eggs: 2})
x = 1
y = 2
if x == 4:
    print(x, y)

# Wrong
spam( ham[ 1 ], { eggs: 2 } )
x             = 1
y             = 2
```

**Avoid extraneous whitespace:**
- Inside brackets: `spam(ham[1])` not `spam( ham[ 1 ] )`
- Before commas/colons: `if x == 4:` not `if x == 4 :`
- Around `=` in keyword arguments: `func(arg=value)` not `func(arg = value)`

### Binary Operators

Break **before** binary operators for readability (Knuth style):

```python
# Correct
income = (gross_wages
          + taxable_interest
          + (dividends - qualified_dividends)
          - ira_deduction)
```

## Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Packages/Modules | `lower_with_under` | `my_module.py` |
| Classes | `CapWords` | `MyClass` |
| Exceptions | `CapWords` + `Error` suffix | `ValidationError` |
| Functions/Methods | `lower_with_under` | `calculate_total()` |
| Constants | `CAPS_WITH_UNDER` | `MAX_VALUE` |
| Instance variables | `lower_with_under` | `self.user_name` |
| Protected | `_single_leading_underscore` | `_internal_method()` |
| Private (name mangling) | `__double_leading` | `__private_attr` |

**Critical rules:**
- Never use `l`, `O`, or `I` as single-character names
- Avoid abbreviations unless universally understood
- Names should reflect usage, not implementation

## Type Annotations

### Always Annotate

```python
from __future__ import annotations
from typing import Any
from collections.abc import Sequence, Mapping

def process_items(
    items: Sequence[str],
    config: Mapping[str, Any] | None = None,
) -> list[str]:
    """Process items with optional config."""
    ...
```

### Key Rules

| Pattern | Correct | Wrong |
|---------|---------|-------|
| Optional | `str | None` | `Optional[str]` (old style) |
| Union | `int | str` | `Union[int, str]` (old style) |
| Collections | `list[int]` | `List[int]` (old style) |
| Abstract types | `Sequence[T]`, `Mapping[K, V]` | `list`, `dict` in signatures |
| Forward reference | Use `from __future__ import annotations` | String quotes |

### Spacing

```python
# Correct
def func(a: int = 0) -> int:  # Space around = with annotation
    code: int  # Space after colon
    
# Wrong  
def func(a:int=0) -> int:  # Missing spaces
```

## Docstrings (PEP 257)

### One-liners

```python
def calculate_area(radius: float) -> float:
    """Return the area of a circle with given radius."""
    return 3.14159 * radius ** 2
```

### Multi-line (Google Style)

```python
def fetch_data(
    url: str,
    timeout: int = 30,
    retries: int = 3,
) -> dict[str, Any]:
    """Fetch data from a remote URL.

    Retrieves JSON data from the specified URL with automatic
    retry logic on transient failures.

    Args:
        url: The URL to fetch data from.
        timeout: Request timeout in seconds.
        retries: Maximum number of retry attempts.

    Returns:
        Parsed JSON response as a dictionary.

    Raises:
        ConnectionError: If unable to connect after all retries.
        ValueError: If response is not valid JSON.
    """
```

### Class Docstrings

```python
class DataProcessor:
    """Process and transform data records.

    Handles validation, transformation, and serialization of
    incoming data records.

    Attributes:
        batch_size: Number of records to process per batch.
        strict_mode: Whether to fail on first error.
    """

    def __init__(self, batch_size: int = 100) -> None:
        """Initialize the processor.

        Args:
            batch_size: Records per batch (default: 100).
        """
```

## Exception Handling

### Use Specific Exceptions

```python
# Correct: Specific exception with context
def connect(port: int) -> Connection:
    if port < 1024:
        raise ValueError(f"Port must be >= 1024, got {port}")
    try:
        return _establish_connection(port)
    except OSError as err:
        raise ConnectionError(f"Failed to connect on port {port}") from err

# Wrong: Bare except, generic exception
try:
    do_something()
except:  # Never do this
    pass
```

### Exception Rules

| Do | Don't |
|----|-------|
| Catch specific exceptions | Use bare `except:` |
| Use `raise X from Y` for chaining | Lose original traceback |
| Add `Error` suffix to exception classes | Name exceptions without `Error` |
| Limit `try` blocks to minimal code | Wrap too much code in `try` |
| Use `finally` for cleanup | Forget to close resources |

### Don't Use Assert for Validation

```python
# Wrong: assert can be disabled with -O
def process(value: int) -> int:
    assert value >= 0, "Value must be non-negative"  # Don't do this
    return value * 2

# Correct: Use explicit validation
def process(value: int) -> int:
    if value < 0:
        raise ValueError(f"Value must be non-negative, got {value}")
    return value * 2
```

## Comprehensions & Generators

### Keep Comprehensions Simple

```python
# Correct: Clear and readable
result = [x**2 for x in range(10) if x % 2 == 0]

# Wrong: Too complex - use a loop instead
result = [(x, y) for x in range(10) for y in range(5) if x * y > 10]
```

### Prefer Generators for Large Data

```python
# Memory efficient
def process_large_file(path: str):
    with open(path) as f:
        yield from (line.strip() for line in f if line.strip())
```

## Resource Management

### Always Use Context Managers

```python
# Correct
with open("file.txt") as f:
    content = f.read()

# Correct: Multiple resources
with (
    open("input.txt") as infile,
    open("output.txt", "w") as outfile,
):
    outfile.write(infile.read())
```

## Mutable Default Arguments

**Never use mutable defaults:**

```python
# Wrong: Mutable default shared across calls
def append_to(element, to=[]):  # Bug!
    to.append(element)
    return to

# Correct: Use None sentinel
def append_to(element, to: list | None = None) -> list:
    if to is None:
        to = []
    to.append(element)
    return to
```

## Modern Python Features

### Use f-strings (Not % or .format)

```python
name = "World"
# Correct
message = f"Hello, {name}!"

# Prefer f-string debugging
value = 42
print(f"{value=}")  # Prints: value=42
```

### Logging: Use %-formatting

```python
import logging

logger = logging.getLogger(__name__)

# Correct: Lazy evaluation, structured logging
logger.info("Processing %d items for user %s", count, user_id)

# Wrong: f-string evaluated even if level disabled
logger.info(f"Processing {count} items for user {user_id}")
```

### Use Walrus Operator When It Improves Readability

```python
# Good use case: Avoid repeated computation
if (n := len(data)) > 10:
    print(f"Processing {n} items")
```

## Code Smells to Avoid

| Smell | Why It's Bad | Fix |
|-------|--------------|-----|
| `from module import *` | Pollutes namespace | Import explicitly |
| Nested functions > 2 deep | Hard to follow | Extract to methods |
| Methods > 50 lines | Too complex | Split into smaller methods |
| `except Exception:` | Catches too much | Catch specific exceptions |
| Global mutable state | Side effects, testing issues | Pass as arguments |
| Magic numbers | Unclear intent | Use named constants |
| Boolean parameters | Unclear at call site | Use enums or keyword args |

## Testing Conventions

```python
import pytest

class TestCalculator:
    """Tests for the Calculator class."""

    def test_add_positive_numbers(self) -> None:
        """Addition of positive numbers returns correct sum."""
        assert Calculator().add(2, 3) == 5

    def test_divide_by_zero_raises(self) -> None:
        """Division by zero raises ZeroDivisionError."""
        with pytest.raises(ZeroDivisionError):
            Calculator().divide(1, 0)
```

## Ruff Rules to Enable

Recommended rule selection for `pyproject.toml`:

```toml
[tool.ruff.lint]
select = [
    "E", "F", "W",      # pycodestyle, Pyflakes
    "I",                # isort
    "UP",               # pyupgrade
    "B",                # flake8-bugbear
    "C4",               # flake8-comprehensions
    "SIM",              # flake8-simplify
    "PT",               # flake8-pytest-style
    "RUF",              # Ruff-specific
]
```

## Quick Reference Checklist

Before committing Python code:

- [ ] All functions have type annotations
- [ ] Public APIs have docstrings with Args/Returns/Raises
- [ ] No bare `except:` clauses
- [ ] No mutable default arguments
- [ ] Imports are organized (stdlib, third-party, local)
- [ ] No magic numbers (use constants)
- [ ] Context managers for resources
- [ ] Specific exception types with helpful messages
- [ ] Line length ≤ 79 characters
- [ ] Passes `ruff check` and `mypy --strict`
