import { defineConfig } from '@playwright/test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

// S1 E2E(M3-5): 빌드된 화면(web/dist)을 FastAPI가 제공하고, 실행마다 빈 임시 workspace를 쓴다.
// 서버 명령은 E2E_SERVER_CMD로 바꿀 수 있다(기본: uv). 사용자의 8000번 서버와 겹치지 않게 8765번을 쓴다.
const PORT = Number(process.env.E2E_PORT ?? 8765);
const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'dpa-e2e-'));
const server =
  process.env.E2E_SERVER_CMD ?? `uv run uvicorn api.app:app --host 127.0.0.1 --port ${PORT}`;

export default defineConfig({
  testDir: 'e2e',
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: { baseURL: `http://127.0.0.1:${PORT}`, viewport: { width: 1440, height: 1000 } },
  webServer: {
    command: server,
    cwd: '..',
    url: `http://127.0.0.1:${PORT}/api/sets`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: { DPA_WORKSPACE: workspace },
  },
});
