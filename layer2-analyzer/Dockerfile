FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY src/ ./

# Create data directory for ChromaDB
RUN mkdir -p /app/data/chromadb

# Run analyzer
CMD ["python", "-u", "main.py"]
