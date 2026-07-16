FROM python:3.14-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends tzdata \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock* ./
COPY apps/upbit_gateway ./apps/upbit_gateway
RUN pip install --no-cache-dir uv \
  && uv sync --frozen --no-dev

ENV TZ=UTC
ENV PYTHONPATH=/app/apps/upbit_gateway
EXPOSE 8001
CMD ["uv", "run", "--no-sync", "uvicorn", "goodmoneying_upbit_gateway.main:app", "--host", "0.0.0.0", "--port", "8001"]
