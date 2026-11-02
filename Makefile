.PHONY: sync test lint paths check hooks

# 테스트는 반드시 tests 경로를 지정한다(scripts/test_*.py 수집 금지, 개발 계획 3절).
sync:
	uv sync --locked

test:
	uv run pytest tests

lint:
	uv run ruff check .

paths:
	uv run python tools/check_tracked_paths.py --tracked

check: paths lint test

hooks:
	git config core.hooksPath .githooks
