FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    CHROME_BIN=/usr/bin/chromium \
    DATA_DIR=/data

RUN apt-get update && apt-get install -y --no-install-recommends \
      chromium curl ca-certificates fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir websocket-client==1.8.0

WORKDIR /app
COPY src/ /app/

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
  CMD python -c "import time,sys,os; p='/tmp/clockwork.heartbeat';\
sys.exit(0 if os.path.exists(p) and time.time()-float(open(p).read())<600 else 1)"

ENTRYPOINT ["/entrypoint.sh"]
