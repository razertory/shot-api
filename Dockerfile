FROM python:3.12-slim

# shot-scraper needs Playwright's Chromium
RUN pip install --no-cache-dir shot-scraper \
    && shot-scraper install \
    && playwright install-deps chromium \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
