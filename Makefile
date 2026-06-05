# Makefile — atajos para los checks de calidad.
# Uso:
#   make check        # ruff + ruff-format + mypy + import-linter + pytest+cov
#   make check-fast   # solo ruff + import-linter + pytest -q
#   make lint         # solo ruff
#   make fmt          # ruff format
#   make typecheck    # mypy estricto
#   make imports      # import-linter
#   make test         # pytest
#   make cov          # pytest + cobertura
#   make install      # pip install -e ".[dev]"

.PHONY: check check-fast lint fmt typecheck imports test cov install

check:
	./scripts/check.sh

check-fast:
	./scripts/check.sh fast

lint:
	ruff check .

fmt:
	ruff format .

typecheck:
	mypy kie_avatar_studio

imports:
	lint-imports

test:
	pytest -q

cov:
	pytest -q --cov=kie_avatar_studio --cov-report=term-missing

install:
	pip install -e ".[dev]"
