FROM python:3.12-slim

WORKDIR /app

# Copy the entire mock_device package
COPY . /app/mock_device/

# Run the mock device as a module
CMD ["python", "-m", "mock_device"]
