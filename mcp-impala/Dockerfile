# Python 3.11 or greater. Default 3.12. Debian slim: impyla needs glibc.
# Build from repo root: docker compose build context `.`, dockerfile `mcp-impala/Dockerfile`.
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim-bookworm
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[mcp,hive]" && rm -rf /build
WORKDIR /app
COPY mcp-impala/sql.py mcp-impala/hs2.py mcp-impala/mcp_impala_server.py mcp-impala/quota.py /app/
ENV PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=2s --timeout=2s --retries=20 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"
CMD ["python", "-u", "/app/mcp_impala_server.py"]
