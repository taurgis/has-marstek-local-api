FROM python:3.12-slim

WORKDIR /app

# Build from the repository root so the mock consumes the integration's
# canonical firmware profile rather than carrying a duplicate capability table.
COPY tools/mock_device /app/mock_device/
COPY custom_components/marstek/firmware_profile.py /app/custom_components/marstek/firmware_profile.py

# Run the mock device as a module
CMD ["python", "-m", "mock_device"]
