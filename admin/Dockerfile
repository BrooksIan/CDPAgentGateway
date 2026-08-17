FROM python:3.12-alpine
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY store.py server.py /app/
COPY static /app/static
ENV PORT=8080
ENV ADMIN_DB=/data/gateway.sqlite
EXPOSE 8080
HEALTHCHECK --interval=2s --timeout=2s --retries=20 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"
CMD ["python", "-u", "/app/server.py"]
