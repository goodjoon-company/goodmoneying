FROM python:3.14-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends tzdata \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock* ./
COPY apps ./apps
COPY packages ./packages
COPY docs ./docs
RUN pip install --no-cache-dir uv \
  && uv sync --frozen --no-dev

ENV TZ=UTC
ENV PYTHONPATH=/app/apps/api:/app/apps/worker:/app/packages/shared
CMD ["sh", "-c", "while true; do uv run --no-sync python -m goodmoneying_worker.realtime_collection_worker; sleep ${GOODMONEYING_REALTIME_COLLECTION_INTERVAL_SECONDS:-60}; done"]
