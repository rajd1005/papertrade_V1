# Use a lightweight Python image
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Install simple dependencies (No Chrome needed!)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the app code
COPY . .

# Run the app
CMD ["python", "main.py"]
