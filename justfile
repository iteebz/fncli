default:
    @just --list

install:
    @uv sync
    @just hooks

hooks:
    @cp scripts/hooks/pre-commit .git/hooks/pre-commit
    @chmod +x .git/hooks/pre-commit

lint:
    #!/bin/bash
    set -e
    uv run ruff format .
    uv run ruff check . --fix
    uv run pyright

ci: lint
    @uv run pytest tests -q --tb=no || [ $? -eq 5 ]

test:
    @uv run pytest tests

build:
    @uv build

clean:
    @rm -rf dist build .pytest_cache .ruff_cache __pycache__ .venv
    @find . -type d -name "__pycache__" -exec rm -rf {} +
