FROM python:3.12-slim

WORKDIR /app

# Python block-buffers stdout into Docker's log pipe. The mock now prints one
# line per request instead of five, so without this the buffer takes far too
# long to fill and `docker logs` looks empty while the device is working.
ENV PYTHONUNBUFFERED=1

# Build from the repository root so the mock consumes the integration's
# canonical firmware profile and Open API command names rather than
# carrying duplicate tables.
COPY tools/mock_device /app/mock_device/
COPY custom_components/marstek/firmware_profile.py /app/custom_components/marstek/firmware_profile.py
COPY custom_components/marstek/pymarstek/const.py /app/custom_components/marstek/pymarstek/const.py
COPY custom_components/marstek/pymarstek/network.py /app/custom_components/marstek/pymarstek/network.py
COPY custom_components/marstek/pymarstek/validators.py /app/custom_components/marstek/pymarstek/validators.py

# Run the mock device as a module
CMD ["python", "-m", "mock_device"]
