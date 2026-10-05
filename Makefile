.PHONY: install local local-check local-prepare dev worker check format test integration openapi migrate sql sql-check infra-up infra-down contracts model-check storage-audit dependency-audit iam-reconcile

install:
	uv sync --locked

local:
	./scripts/start-local.sh

local-check:
	./scripts/start-local.sh --check

local-prepare:
	./scripts/start-local.sh --prepare-only

dev:
	uv run uvicorn creativity_service.app:create_app --factory --reload --host 127.0.0.1 --port 8000 --no-access-log

worker:
	uv run celery -A creativity_service.workers.app:app worker --pool=solo --loglevel=INFO --hostname=creativity@%h

check:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	uv run pytest -m 'not integration'
	uv run creativity-openapi --check
	uv run creativity-openapi --backend --check
	uv run python -m creativity_service.core.contracts.export --check
	uv run python -m creativity_service.modules.integrations.export --check
	uv run python -m creativity_service.modules.channels.export --check
	uv run python -m creativity_service.modules.usage.export --check
	uv run python -m creativity_service.modules.agents.export --check
	uv run python -m creativity_service.modules.skills.export --check
	uv run python -m creativity_service.modules.tools.export --check
	uv run python -m creativity_service.modules.mcp.export --check
	uv run python -m creativity_service.modules.runs.export --check
	uv run python -m creativity_service.modules.memory.export --check
	uv run python -m creativity_service.modules.conversations.export --check
	uv run python -m creativity_service.modules.prompts.export --check
	uv run python -m creativity_service.modules.models.export --check
	uv run python -m creativity_service.modules.evaluations.export --check
	uv run python scripts/render_data_model.py --check
	uv run python scripts/render_init_sql.py --check
	uv run python -m creativity_service.core.database.audit

format:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run pytest -m 'not integration'

integration:
	uv run pytest -m integration

openapi:
	uv run creativity-openapi
	uv run creativity-openapi --backend

migrate:
	uv run alembic upgrade head

sql:
	uv run python scripts/render_init_sql.py

sql-check:
	uv run python scripts/render_init_sql.py --check

infra-up:
	./scripts/start-local.sh --infra-only

infra-down:
	./scripts/start-local.sh --stop-infra

contracts:
	uv run python -m creativity_service.core.contracts.export
	uv run python -m creativity_service.modules.integrations.export
	uv run python -m creativity_service.modules.channels.export
	uv run python -m creativity_service.modules.usage.export
	uv run python -m creativity_service.modules.agents.export
	uv run python -m creativity_service.modules.skills.export
	uv run python -m creativity_service.modules.tools.export
	uv run python -m creativity_service.modules.mcp.export
	uv run python -m creativity_service.modules.runs.export
	uv run python -m creativity_service.modules.memory.export
	uv run python -m creativity_service.modules.conversations.export
	uv run python -m creativity_service.modules.prompts.export
	uv run python -m creativity_service.modules.models.export
	uv run python -m creativity_service.modules.evaluations.export
	uv run creativity-openapi
	uv run creativity-openapi --backend

model-check:
	uv run python scripts/render_data_model.py --check
	uv run python scripts/render_init_sql.py --check
	uv run python -m creativity_service.core.database.audit

storage-audit:
	uv run python -m creativity_service.core.database.audit --database

dependency-audit:
	uv run pip-audit --local --progress-spinner off

iam-reconcile:
	uv run creativity-iam reconcile-revocations

.PHONY: channels-init
channels-init:
	uv run creativity-channels init-system

.PHONY: scheduler
scheduler:
	uv run celery -A creativity_service.workers.app:app beat --loglevel=INFO
