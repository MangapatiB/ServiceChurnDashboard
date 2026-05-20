FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    HOME=/tmp \
    LOG_DIR=/tmp/logs

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc g++ unixodbc-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir gunicorn

COPY . .

RUN mkdir -p /tmp/logs /app/logs \
    && chgrp -R 0 /app /tmp \
    && chmod -R g=u /app /tmp

EXPOSE 8080

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --workers 2 --threads 4 --timeout 120 run:app"]