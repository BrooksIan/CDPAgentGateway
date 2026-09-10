# Python 3.11 or greater. Default 3.12.
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-alpine
WORKDIR /app
COPY mock_cdp_server.py /app/mock_cdp_server.py
EXPOSE 8080
CMD ["python", "-u", "/app/mock_cdp_server.py"]
