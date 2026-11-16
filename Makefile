.PHONY: install web test preview

install:
	uv sync --all-extras
	cd web && npm ci

# The built UI is committed (src/axial/web_dist) so git installs need no Node.
web:
	cd web && npm run build

test:
	uv run pytest -q
	cd web && npm run typecheck

# Render the test project with fixture data
preview: web
	cd tests/project && uv run axial preview booking_ops --fixture
