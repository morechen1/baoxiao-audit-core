.PHONY: install format lint typecheck test up down migrate seed review-demo demo demo-smoke

install:
	python3.12 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

format:
	.venv/bin/ruff format app tests scripts
	.venv/bin/ruff check --fix app tests scripts

lint:
	.venv/bin/ruff check app tests scripts

typecheck:
	.venv/bin/mypy app/core app/models app/repositories app/services app/providers

test:
	.venv/bin/pytest

up:
	docker compose up -d --build

down:
	docker compose down

migrate:
	docker compose exec api alembic upgrade head

seed:
	docker compose exec api python -m app.cli.main seed

review-demo:
	docker compose exec api python -m app.cli.main seed
	docker compose exec api python -m app.cli.main export-review-batch --data-type penalty --format jsonl

demo:
	./scripts/start_demo.sh

demo-smoke:
	BAOXIAO_DEMO_RUNTIME=1 DATABASE_URL="$${DEMO_DATABASE_URL:-postgresql+psycopg://$${USER}@localhost:5432/baoxiao_demo}" DATA_DIR=.demo-data .venv/bin/python scripts/demo_smoke.py
