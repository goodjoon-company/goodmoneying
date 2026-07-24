FROM python:3.14-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends tzdata \
  && rm -rf /var/lib/apt/lists/* \
  && useradd --create-home --uid 10001 --shell /usr/sbin/nologin app

WORKDIR /app
COPY pyproject.toml uv.lock* ./
COPY apps/upbit_gateway ./apps/upbit_gateway
RUN pip install --no-cache-dir uv \
  && uv sync --frozen --no-dev \
  && chown -R app:app /app

ENV TZ=UTC
ENV PYTHONPATH=/app/apps/upbit_gateway
ENV PATH=/app/.venv/bin:$PATH
EXPOSE 8001
USER app
CMD ["uvicorn", "goodmoneying_upbit_gateway.main:app", "--host", "0.0.0.0", "--port", "8001"]
