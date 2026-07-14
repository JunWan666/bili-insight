PYTHON ?= python3
VENV_PYTHON := backend/.venv/bin/python

.PHONY: help bootstrap dev test lint audit build compose-config docker-build docker-up docker-down docker-logs

help:
	@echo "bootstrap       Install backend and frontend development dependencies"
	@echo "dev             Start FastAPI and Vite on loopback interfaces"
	@echo "test            Run backend and frontend test suites"
	@echo "lint            Run backend and frontend static checks"
	@echo "audit           Audit Python and Node dependencies for known vulnerabilities"
	@echo "build           Compile the backend and build frontend production assets"
	@echo "compose-config  Validate the resolved Docker Compose configuration"
	@echo "docker-build    Build both production container images"
	@echo "docker-up       Build and start the production stack"
	@echo "docker-down     Stop the production stack without deleting volumes"
	@echo "docker-logs     Follow sanitized application logs"

bootstrap:
	$(PYTHON) -c "import sys; assert (3, 12) <= sys.version_info < (4, 0), 'Python 3.12 or newer is required'"
	$(PYTHON) -m venv backend/.venv
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -e "./backend[dev]"
	cd frontend && npm ci --no-audit --no-fund
	@command -v ffmpeg >/dev/null
	@command -v ffprobe >/dev/null

dev:
	sh ./scripts/start-dev.sh

test:
	cd backend && .venv/bin/python -m pytest
	cd frontend && npm run test

lint:
	cd backend && .venv/bin/python -m ruff check .
	cd backend && .venv/bin/python -m ruff format --check .
	cd backend && .venv/bin/python -m mypy app
	cd frontend && npm run lint
	cd frontend && npm run typecheck

audit:
	cd backend && .venv/bin/python -m pip_audit . --strict --progress-spinner off
	cd backend && .venv/bin/python -m pip_audit --local --skip-editable --progress-spinner off
	cd frontend && npm audit --audit-level=high

build:
	$(VENV_PYTHON) -m compileall -q backend/app
	cd frontend && npm run build

compose-config:
	docker compose --env-file .env.example config --quiet

docker-build:
	docker compose build

docker-up:
	docker compose up --detach --build --wait

docker-down:
	docker compose down

docker-logs:
	docker compose logs --follow --tail=200
