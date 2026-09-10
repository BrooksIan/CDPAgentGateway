# Python 3.11 or greater. Default 3.12.
# Build from repo root: docker compose build context `.`, dockerfile `mcp-spark/Dockerfile`.
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-alpine
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[mcp]" && rm -rf /build
WORKDIR /app
COPY mcp-spark/livy.py mcp-spark/mcp_spark_server.py mcp-spark/quota.py /app/
ENV PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=2s --timeout=2s --retries=20 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"
CMD ["python", "-u", "/app/mcp_spark_server.py"]
