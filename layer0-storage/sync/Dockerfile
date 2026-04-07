FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY log_archiver.py .

# Run archiver
CMD ["python", "-u", "log_archiver.py"]
