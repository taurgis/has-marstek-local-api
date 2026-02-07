# Contributing

Thanks for contributing! Please keep changes focused and follow the existing style.

## Development setup

```bash
# Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements_test.txt
```

## Verification

Run these before opening a PR:

```bash
# Linting
python3 -m ruff check custom_components/marstek/

# Type checking
python3 -m mypy --strict custom_components/marstek/

# Tests with coverage
pytest tests/ -q --cov=custom_components/marstek --cov-fail-under=95
```

## Tips

- Add or update tests for changes in behavior.
- Keep user-facing strings in sync with translations.
- Prefer small, well-scoped commits for easier review.
