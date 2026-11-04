FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.19
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY fasttrade ./fasttrade
COPY data ./data
RUN useradd --create-home --uid 10001 fasttrade && mkdir -p /home/fasttrade/.fasttrade && chown fasttrade:fasttrade /home/fasttrade/.fasttrade
ENV PATH="/app/.venv/bin:$PATH" HOME=/home/fasttrade
USER fasttrade
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=3).read()"
EXPOSE 8765
CMD ["uvicorn", "fasttrade.web:app", "--host", "127.0.0.1", "--port", "8765", "--no-proxy-headers", "--no-access-log", "--timeout-graceful-shutdown", "100"]
