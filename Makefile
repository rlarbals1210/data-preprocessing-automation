.PHONY: sync test lint paths check hooks web serve dev score-dev measure-reuse e2e

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

# 화면 빌드(Node 필요) → web/dist. serve는 web/dist가 있으면 화면까지 http://127.0.0.1:8000 에서 제공한다.
web:
	cd web && npm ci && npm run build

serve:
	uv run uvicorn api.app:app --host 127.0.0.1 --port 8000

# 개발: API(8000)와 Vite 개발 서버(5173, /api 프록시)를 함께 띄운다.
dev:
	(trap 'kill 0' INT; uv run uvicorn api.app:app --host 127.0.0.1 --port 8000 --reload & cd web && npm run dev)

# 개발셋 13개를 서비스 흐름으로 처리해 채점한다(M2-4). 개발 정답은 채점기만 읽는다. 평가 분할은 쓰지 않는다.
score-dev:
	uv run python -m tools.score_dev

# 규칙 재사용의 검토 감소(④ 6-1, M6-8): 개발 입력을 고객 단위 A·B로 나눠 두 조건을 측정한다(정답 안 읽음).
measure-reuse:
	uv run python -m tools.measure_reuse

# S1 화면 E2E(M3-5): 화면을 빌드하고 임시 workspace로 서버(8765번)를 띄워 Playwright로 확인한다.
# 처음 한 번은 브라우저 설치가 필요하다: cd web && npx playwright install chromium
e2e:
	cd web && npm run build && npm run e2e
