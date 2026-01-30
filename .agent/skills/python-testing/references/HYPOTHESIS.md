# Hypothesis Property-Based Testing Reference

Generate test cases automatically to find edge cases.

## Basic Usage

```python
from hypothesis import given, strategies as st

@given(st.integers())
def test_abs_is_non_negative(n: int) -> None:
    assert abs(n) >= 0

@given(st.lists(st.integers()))
def test_sort_preserves_length(lst: list[int]) -> None:
    assert len(sorted(lst)) == len(lst)
```

## Common Strategies

```python
from hypothesis import strategies as st

# Primitives
st.integers()                    # Any integer
st.integers(min_value=0, max_value=100)  # Bounded
st.floats()                      # Any float
st.floats(allow_nan=False, allow_infinity=False)
st.text()                        # Unicode strings
st.text(min_size=1, max_size=50)
st.binary()                      # Bytes
st.booleans()                    # True/False

# Collections
st.lists(st.integers())          # List of integers
st.lists(st.integers(), min_size=1, max_size=10)
st.tuples(st.integers(), st.text())
st.dictionaries(st.text(), st.integers())
st.sets(st.integers())

# Combining strategies
st.integers() | st.none()        # Optional integer
st.one_of(st.integers(), st.text())  # Union type

# Filtering
st.integers().filter(lambda x: x % 2 == 0)  # Even only

# Mapping
st.integers().map(lambda x: x * 2)  # Transform values
```

## assume() for Filtering in Tests

```python
from hypothesis import given, assume, strategies as st

@given(st.integers(), st.integers())
def test_division(a: int, b: int) -> None:
    assume(b != 0)  # Skip if b is 0
    result = a / b
    assert result * b == pytest.approx(a)
```

## Composite Strategies

```python
from hypothesis import strategies as st

@st.composite
def ordered_pair(draw):
    """Generate two integers where a <= b."""
    a = draw(st.integers())
    b = draw(st.integers(min_value=a))
    return (a, b)

@given(ordered_pair())
def test_ordered_pair(pair: tuple[int, int]) -> None:
    a, b = pair
    assert a <= b
```

## Settings and Examples

```python
from hypothesis import given, settings, example, strategies as st

@given(st.text())
@settings(max_examples=500)  # Run more examples
def test_with_more_examples(s: str) -> None:
    ...

@given(st.integers())
@example(0)       # Always test these specific cases
@example(-1)
@example(2**31)
def test_with_examples(n: int) -> None:
    ...
```

## Strategy Quick Reference

| Strategy | Generates |
|----------|-----------|
| `st.integers()` | Integers |
| `st.floats()` | Floating point numbers |
| `st.text()` | Unicode strings |
| `st.binary()` | Byte strings |
| `st.booleans()` | True/False |
| `st.none()` | None |
| `st.lists(s)` | Lists of strategy s |
| `st.tuples(s1, s2)` | Tuples |
| `st.dictionaries(k, v)` | Dicts with key/value strategies |
| `st.sets(s)` | Sets |
| `st.one_of(s1, s2)` | Union of strategies |
| `s1 \| s2` | Same as one_of |
| `st.just(x)` | Always returns x |
| `st.sampled_from([...])` | Pick from list |

## When to Use Property-Based Testing

Good candidates:
- Serialization/deserialization roundtrips
- Mathematical properties (commutativity, associativity)
- Sorting and searching algorithms
- Data transformations
- Parser/validator edge cases

Not ideal for:
- Simple CRUD operations
- External API integration tests
- UI/UX testing
