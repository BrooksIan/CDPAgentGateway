# Python 3.11 or greater. Default 3.12. Debian slim: impyla needs glibc.
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim-bookworm
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY sql.py hs2.py server.py quota.py /app/
ENV PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=2s --timeout=2s --retries=20 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"
CMD ["python", "-u", "/app/server.py"]
