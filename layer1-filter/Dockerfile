FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY config/ ./config/
COPY src/ ./src/

# Create data directories
RUN mkdir -p /app/data /app/models

# Download LogBERT model (reuse from logbert service)
# Model will be mounted as volume

CMD ["python", "-u", "src/main.py"]
