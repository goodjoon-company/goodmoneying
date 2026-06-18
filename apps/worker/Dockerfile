FROM python:3.14-slim

WORKDIR /app
COPY pyproject.toml uv.lock* ./
COPY apps ./apps
COPY packages ./packages
COPY docs ./docs
RUN pip install --no-cache-dir uv \
  && uv sync --frozen --no-dev

ENV PYTHONPATH=/app/apps/api:/app/apps/worker:/app/packages/shared
CMD ["uv", "run", "--no-sync", "python", "-m", "goodmoneying_worker.main", "--once"]
