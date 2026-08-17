# Python 3.11 or greater. Default 3.12.
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-alpine
WORKDIR /app
COPY server.py /app/server.py
EXPOSE 8080
CMD ["python", "-u", "/app/server.py"]
