.PHONY: setup-local start lint
export PYTHONPATH := src
ifneq (,$(wildcard .env))
    include .env
    export
endif

setup-local:
	@echo "🔧 Installing Poetry (if not already installed)..."
	@export PATH="$$HOME/.local/bin:$$PATH"; \
	if ! command -v poetry &> /dev/null; then pip install --user poetry; fi; \
	echo "📂 Creating venv manually in ./venv ..."; \
	python3.12 -m venv venv; \
	source venv/bin/activate; \
	echo "📦 Configuring Poetry to use that venv..."; \
	poetry env use ./venv/bin/python; \
	echo "📦 Installing dependencies..."; \
	poetry install; \
	echo "✅ Setup complete!"; \
	poetry env info

start:
	poetry run python -m main

start-prod:
	poetry run alembic -c migrations/alembic.ini upgrade head
	poetry run python -m main

makemigrations:
	poetry run alembic -c migrations/alembic.ini revision --autogenerate

migrate:
	poetry run alembic -c migrations/alembic.ini upgrade head

lint:
	poetry run black ./
	poetry run isort ./
	poetry run ruff check ./
	poetry run mypy .