import { expect, test, type APIRequestContext } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// SC12 재사용 규칙(M6-7): 단위별 규칙 목록에서 승인을 취소하면 준비 중인 실행의 미리 선택이 해제되고
// 다음 실행부터 그 규칙이 쓰이지 않는다(AC-09-4). 개발 입력만 쓴다.
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
  return { run, preview: await (await request.get(`/api/runs/${run.id}/preview`)).json() };
}

test('SC12: 승인 취소는 준비 중인 실행의 미리 선택을 해제한다', async ({ page, request }) => {
  const source = `SC12-${Date.now()}`;
  const first = await prepare(request, source);
  await request.post(`/api/runs/${first.run.id}/execute`, {
    data: { approval: first.preview.approval, reuse: { groups: [PHONE], mappings: [] } },
  });
  const { run, preview } = await prepare(request, source);
  expect(preview.groups.find((g: { id: string }) => g.id === PHONE).reuse).toBeGreaterThan(0);

  await page.goto('/reuse');
  const unit = page.getByTestId('reuse-unit').filter({ hasText: source });
  const rule = unit.getByTestId('reuse-rule').filter({ hasText: 'phone' });
  await expect(rule.getByTestId('rule-state')).toHaveText('효력');
  await rule.getByRole('button', { name: '승인 취소' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('사유').fill('오적용 확인');
  await dialog.getByRole('button', { name: '승인 취소' }).click();
  await expect(rule.getByTestId('rule-state')).toHaveText('승인 취소');
  await expect(page.getByTestId('revoke-affected')).toContainText(run.id);

  const after = await (await request.get(`/api/runs/${run.id}/preview`)).json();
  expect(after.groups.find((g: { id: string }) => g.id === PHONE).reuse).toBe(0);
  const stale = await request.post(`/api/runs/${run.id}/execute`, { data: { approval: preview.approval } });
  expect(stale.status()).toBe(409);
});
