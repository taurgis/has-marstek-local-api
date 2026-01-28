# Development

## Repo layout

- `custom_components/marstek/` — integration
- `custom_components/marstek/pymarstek/` — UDP client
- `tools/mock_device/` — mock device for local testing
- `docs/marstek_device_openapi.MD` — protocol reference

## Running tests

From repo root:

```
pytest
```

## Mock device

Run the mock device to develop without hardware:

```
cd tools/mock_device
python mock_marstek.py
```

## Protocol reference

See `docs/marstek_device_openapi.MD`.
