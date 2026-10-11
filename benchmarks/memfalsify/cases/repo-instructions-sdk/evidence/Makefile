.PHONY: install lint format typecheck test test-live check build clean

install:
	uv sync --all-extras

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy

test:
	uv run pytest --cov --cov-report=term-missing

test-live:
	uv run pytest -m live

check: lint typecheck test

build:
	uv build

clean:
	rm -rf dist .coverage .mypy_cache .pytest_cache
