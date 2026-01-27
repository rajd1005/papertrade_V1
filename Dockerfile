# Upgrade to Python 3.12 (Debian 12 Bookworm) to support pandas_ta requirements
FROM python:3.12-slim-bookworm

# 1. Install System Dependencies & Google Chrome
# Uses the modern 'signed-by' keyring method to avoid apt-key deprecation errors
RUN apt-get update && apt-get install -y wget gnupg unzip curl && \
    wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | \
    gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
    > /etc/apt/sources.list.d/google-chrome.list && \
    apt-get update && \
    apt-get install -y google-chrome-stable && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 2. Set working directory
WORKDIR /app

# 3. Install Python Dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy Application Code
COPY . .

# 5. Production Entrypoint
# Using Gunicorn with threads enabled for concurrent handling
CMD ["gunicorn", "-w", "1", "--threads", "8", "-b", "0.0.0.0:8080", "main:app"]
