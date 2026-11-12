.PHONY: install web test check serve preview

install:
	uv sync
	cd web && npm ci

web:
	cd web && npm run build

test:
	uv run pytest -q
	cd web && npm run typecheck

check:
	uv run dash check

serve: web
	uv run dash serve

preview: web
	uv run dash preview booking_ops --fixture
