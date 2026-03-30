default:
    @just --list

install:
    @uv sync
    @just hooks

hooks:
    @cp scripts/hooks/pre-commit .git/hooks/pre-commit
    @chmod +x .git/hooks/pre-commit

format:
    uv run ruff format . && uv run ruff check --fix . || true

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

release VERSION: ci
    #!/usr/bin/env bash
    set -euo pipefail
    VERSION="{{VERSION}}"
    if ! echo "$VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
      echo "error: version must be semver (e.g. 0.0.2), got: $VERSION"
      exit 1
    fi
    if git tag -l "v$VERSION" | grep -q .; then
      echo "error: tag v$VERSION already exists"
      exit 1
    fi
    sed -i '' "s/^version = .*/version = \"$VERSION\"/" pyproject.toml
    git diff --quiet pyproject.toml || \
      git commit pyproject.toml -m "release(fncli): v$VERSION"
    git tag "v$VERSION"
    rm -rf dist
    uv build
    uv publish
    echo "published fncli v$VERSION"
