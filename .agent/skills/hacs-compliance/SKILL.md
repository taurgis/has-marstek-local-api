```skill
---
name: hacs-compliance
description: HACS requirements, manifest fields, CI workflows, and repository structure for custom component distribution
---

# HACS Compliance

Use this skill when ensuring the integration meets HACS (Home Assistant Community Store) requirements for distribution.

## When to Use
- Publishing or updating the integration for HACS
- Adding/checking CI workflows (hassfest, HACS validation, tests)
- Ensuring manifest and repository structure are correct
- Preparing for HACS default repository inclusion

## Repository Structure

Required structure for HACS custom integrations:

```
custom_components/
└── marstek/
    ├── __init__.py
    ├── manifest.json      # Required: integration metadata
    ├── config_flow.py
    ├── strings.json
    ├── translations/
    │   └── en.json
    └── ...
hacs.json                  # Optional but recommended
README.md                  # Required: user documentation
```

## manifest.json Requirements

All required fields for HACS compliance:

```json
{
  "domain": "marstek",
  "name": "Marstek",
  "codeowners": ["@taurgis"],
  "config_flow": true,
  "documentation": "https://github.com/taurgis/has-marstek-local-api",
  "issue_tracker": "https://github.com/taurgis/has-marstek-local-api/issues",
  "iot_class": "local_polling",
  "version": "0.1.0",
  "requirements": []
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `domain` | Yes | Unique identifier, lowercase |
| `name` | Yes | Display name |
| `codeowners` | Yes | GitHub usernames with @ prefix |
| `config_flow` | Yes | Must be `true` for UI setup |
| `documentation` | Yes | Link to README or docs |
| `issue_tracker` | Recommended | GitHub issues URL |
| `iot_class` | Yes | One of: `local_polling`, `local_push`, `cloud_polling`, etc. |
| `version` | Yes | Semantic version string |
| `requirements` | Yes | Python package dependencies (empty array if none) |

## hacs.json (Optional)

Recommended for better HACS integration:

```json
{
  "name": "Marstek Energy Storage",
  "render_readme": true,
  "homeassistant": "2024.1.0",
  "iot_class": "local_polling"
}
```

| Field | Purpose |
|-------|---------|
| `name` | Display name in HACS |
| `render_readme` | Show README in HACS UI |
| `homeassistant` | Minimum HA version |
| `iot_class` | Redundant but helpful for HACS display |

## CI Workflows

### Required: Hassfest Validation

`.github/workflows/hassfest.yaml`:
```yaml
name: Hassfest

on:
  push:
  pull_request:

jobs:
  hassfest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: home-assistant/actions/hassfest@master
```

### Required: HACS Validation

`.github/workflows/hacs.yaml`:
```yaml
name: HACS

on:
  push:
  pull_request:
  schedule:
    - cron: "0 0 * * *"

jobs:
  hacs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: hacs/action@main
        with:
          category: integration
```

### Recommended: Tests Workflow

`.github/workflows/tests.yaml`:
```yaml
name: Tests

on:
  push:
    branches: [main, master]
  pull_request:

jobs:
  tests:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: pip install -r requirements_test.txt
      - run: pytest tests/ -v --cov=custom_components/marstek
```

## GitHub Repository Requirements

Your repository must meet these requirements for HACS:

- **Repository is public** on GitHub
- **Has a description** - Brief summary of what the integration does
- **Issues enabled** - Users need a way to report problems
- **Topics defined** - Add relevant topics like `home-assistant`, `hacs`, `custom-component`
- **Has at least one release** - Full GitHub release (not just a tag)
- **Not archived** - Repository must be active

## HACS Default Repository Inclusion

For inclusion in HACS default repositories (optional, higher bar):

1. **All CI workflows pass** - hassfest, HACS action, tests (no errors or ignores)
2. **GitHub releases** - Use semantic versioning tags (v0.1.0) - releases required, not just tags
3. **Home Assistant Brands** - Submit to [home-assistant/brands](https://github.com/home-assistant/brands) for icon/logo
4. **Quality documentation** - Clear README with installation, configuration, usage
5. **Active maintenance** - Responsive to issues and PRs
6. **Owner/major contributor submits PR** - Only repo owners or major contributors can submit

### Submitting to HACS Default

1. Fork [hacs/default](https://github.com/hacs/default)
2. Create a new branch from `master` (never use master directly)
3. Add repository URL to `integration` file (alphabetically sorted)
4. Submit PR with editable permissions (not from organization account)
5. Fill out PR template completely and accurately

## Version Management

- Use semantic versioning: `MAJOR.MINOR.PATCH`
- Update `manifest.json` version before each release
- Create GitHub releases with matching tags
- HACS reads version from manifest.json and GitHub releases

## Checklist

- [ ] `manifest.json` has all required fields
- [ ] `documentation` and `issue_tracker` URLs are current
- [ ] `hacs.json` present with correct metadata
- [ ] `.github/workflows/hassfest.yaml` exists and passes
- [ ] `.github/workflows/hacs.yaml` exists and passes
- [ ] README has installation instructions for HACS
- [ ] Version in manifest.json matches release tags

```
