import { expect, test, type APIRequestContext } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// SC07 재사용 미리 선택(M6-6b, M6-D4): 표준화 재사용 규칙이 있는 양식은 규칙과 맞는 묶음만 미리 선택되고,
// 새 묶음은 선택 해제로 시작한다. 미리 선택도 최종 확인 전에는 적용되지 않는다(AC-09-3). 개발 입력만 쓴다.
const DEV = path.resolve('..', 'data', 'dirty', 'development');
const DATA = fs.readFileSync(path.join(DEV, 'customers.csv'));
const PHONE = 'customers.phone.pattern';

async function prepare(request: APIRequestContext, source: string) {
  const run = await (
    await request.post('/api/runs', {
      multipart: { set: 'shop', source, files: { name: 'customers.csv', mimeType: 'text/csv', buffer: DATA } },
    })
  ).json();
  const files = run.files.map((f: { item_id: number; mapping: object }) => ({ item_id: f.item_id, ...f.mapping }));
  await request.put(`/api/runs/${run.id}/mapping`, { data: { files } });
  await request.post(`/api/runs/${run.id}/diagnose`);
  return run;
}

test('SC07: 재사용 규칙과 맞는 묶음만 미리 선택된다', async ({ page, request }) => {
  const source = `SC07P-${Date.now()}`;
  const first = await prepare(request, source);
  const p = await (await request.get(`/api/runs/${first.id}/preview`)).json();
  await request.post(`/api/runs/${first.id}/execute`, {
    data: { approval: p.approval, reuse: { groups: [PHONE], mappings: [] } },
  });

  const run = await prepare(request, source);
  await page.goto(`/runs/${run.id}/preview`);
  const phone = page.getByTestId('group-row').filter({ has: page.getByRole('checkbox', { name: PHONE, exact: true }) });
  await expect(phone.getByTestId('group-reuse')).toBeVisible();
  await expect(phone.getByRole('checkbox', { name: PHONE, exact: true })).toBeChecked();
  const others = page.getByTestId('group-row').filter({ hasNot: page.getByTestId('group-reuse') });
  await expect(others.first()).toBeVisible();
  for (const box of await others.getByRole('checkbox').all()) await expect(box).not.toBeChecked();
  await page.getByRole('button', { name: `${PHONE} 셀 보기` }).click();
  await expect(page.getByTestId('cell-reuse').first()).toBeVisible();
  expect((await (await request.get(`/api/runs/${run.id}`)).json()).status).toBe('awaiting_approval');
});
