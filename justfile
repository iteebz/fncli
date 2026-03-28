default:
    @just --list

install:
    @uv sync
    @just hooks

hooks:
    @cp scripts/hooks/pre-commit .git/hooks/pre-commit
    @chmod +x .git/hooks/pre-commit

format:
    uv run ruff format . && uv run ruff check --fix .

lint:
    uv run ruff check .

typecheck:
    uv run pyright

ci: lint typecheck
    @uv run pytest tests -q --tb=no || [ $? -eq 5 ]

test:
    @uv run pytest tests

build:
    @uv build

clean:
    @rm -rf dist build .pytest_cache .ruff_cache __pycache__ .venv
    @find . -type d -name "__pycache__" -exec rm -rf {} +
