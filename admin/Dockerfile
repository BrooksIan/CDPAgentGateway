# Python 3.11 or greater. Default 3.12.
# Build from repo root: docker compose build context `.`, dockerfile `admin/Dockerfile`.
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-alpine
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[admin]" && rm -rf /build
WORKDIR /app
COPY admin/store.py admin/operator_admin_server.py /app/
COPY admin/static /app/static
ENV PORT=8080
ENV ADMIN_DB=/data/gateway.sqlite
EXPOSE 8080
HEALTHCHECK --interval=2s --timeout=2s --retries=20 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"
CMD ["python", "-u", "/app/operator_admin_server.py"]
