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
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "goodmoneying_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
