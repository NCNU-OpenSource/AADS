FROM pytorch/pytorch:2.7.0-cuda12.8-cudnn9-runtime

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/

# Create output directory
RUN mkdir -p /app/output

# Run the anomaly detector with unbuffered output
CMD ["python", "-u", "src/main.py"]
