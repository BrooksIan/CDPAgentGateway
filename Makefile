PYTHON ?= python3
export PYTHONPATH := src$(if $(PYTHONPATH),:$(PYTHONPATH))
CLI ?= $(PYTHON) -m agentgateway
GATEWAY_URL ?= http://127.0.0.1:9080
export GATEWAY_URL

.PHONY: keys config up down logs test test-unit live fetch-jwks doctor status knox

keys:
	$(CLI) init

config:
	$(CLI) config

up:
	$(CLI) up

down:
	$(CLI) down

logs:
	$(CLI) logs -f

status:
	$(CLI) status

doctor:
	$(CLI) doctor --ping

test-unit:
	$(CLI) test --unit

test:
	$(CLI) test

live:
	$(CLI) test --live

fetch-jwks:
	$(CLI) fetch-jwks --jwks-url "$(KNOX_JWKS_URL)" --insecure

knox:
	$(CLI) knox "$(URL)"
