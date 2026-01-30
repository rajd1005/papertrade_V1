# Use Python 3.9 as base
FROM python:3.9

# 1. Install Chrome and Dependencies (Modern GPG method)
RUN apt-get update && apt-get install -y wget gnupg unzip && \
    wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list && \
    apt-get update && \
    apt-get install -y google-chrome-stable

# 2. Set working directory
WORKDIR /app

# 3. Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy all project files
COPY . .

# Old/Current (Risk of multiple workers):
# CMD ["gunicorn", "main:app"]

# New (Safe & Liberated):
CMD ["gunicorn", "-w", "1", "--threads", "8", "-b", "0.0.0.0:8080", "main:app"]
