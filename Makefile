.PHONY: install backend frontend health lint build test

install: install-backend install-frontend

install-backend:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

install-frontend:
	cd frontend && npm ci

backend:
	cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

frontend:
	cd frontend && npm run dev

health:
	curl -s http://127.0.0.1:8000/health

lint:
	cd frontend && npm run lint

build:
	cd frontend && npm run build

test:
	cd backend && .venv/bin/python -m pytest
