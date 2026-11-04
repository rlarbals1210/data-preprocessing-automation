import { expect, test, type APIRequestContext } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// S5 되돌리기(M5-3): 같은 출처에서 두 번 정제한 뒤 실행 이력 화면에서 첫 버전으로 되돌린다. 개발 입력만 쓴다.
const DEV = path.resolve('..', 'data', 'dirty', 'development');

async function runOnce(request: APIRequestContext, source: string, approveAll: boolean) {
  const run = await (
    await request.post('/api/runs', {
      multipart: {
        set: 'shop',
        source,
        files: { name: 'categories.csv', mimeType: 'text/csv', buffer: fs.readFileSync(path.join(DEV, 'categories.csv')) },
      },
    })
  ).json();
  const files = run.files.map((f: { item_id: number; mapping: object }) => ({ item_id: f.item_id, ...f.mapping }));
  await request.put(`/api/runs/${run.id}/mapping`, { data: { files } });
  await request.post(`/api/runs/${run.id}/diagnose`);
  if (!approveAll) await request.put(`/api/runs/${run.id}/selection`, { data: { approved_groups: [], excluded_cells: [] } });
  const p = await (await request.get(`/api/runs/${run.id}/preview`)).json();
  await request.post(`/api/runs/${run.id}/execute`, { data: { approval: p.approval } });
  return run.id as string;
}

test('S5: 실행 이력에서 이전 버전으로 되돌리면 새 복원 버전이 현재가 되고 기록은 남는다', async ({ page, request }) => {
  const source = `S5-${Date.now()}`;
  await runOnce(request, source, false);
  await runOnce(request, source, true);
  const sid = (await (await request.get('/api/sources')).json()).find((s: { name: string }) => s.name === source).id;
  const before = await (await request.get(`/api/sources/${sid}/versions`)).json();
  expect(before.length).toBe(2);

  await page.goto(`/history?source=${sid}`);
  const rows = page.getByTestId('version-row');
  await expect(rows).toHaveCount(2);
  await expect(rows.first().getByTestId('current')).toBeVisible(); // 최신이 위, 현재 표시
  await rows.nth(1).getByRole('button', { name: '되돌리기' }).click();
  await expect(page.getByRole('dialog')).toContainText('이미 내려받은 파일은 회수되지 않습니다');
  await page.getByRole('dialog').getByRole('button', { name: '되돌리기' }).click();

  await expect(rows).toHaveCount(3);
  await expect(rows.first()).toContainText(`복원 ← v${before[0].id}`);
  await expect(rows.first().getByTestId('current')).toBeVisible();
  await expect(page.getByTestId('version-detail')).toContainText(`v${before[0].id}과 같은 데이터`);

  const after = await (await request.get(`/api/sources/${sid}/versions`)).json();
  expect(after.slice(0, 2).map((v: { id: number }) => v.id)).toEqual(before.map((v: { id: number }) => v.id));
  expect(after[2].data_hash).toBe(before[0].data_hash); // AC-08-2
  const a = await (await request.get(`/api/versions/${before[0].id}/files/categories.csv`)).text();
  const b = await (await request.get(`/api/versions/${after[2].id}/files/categories.csv`)).text();
  expect(b).toBe(a);
});
