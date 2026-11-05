import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// SC07 재사용 승인(M6-5): 미리보기에서 묶음·매핑을 "다음에도 사용"으로 고르고 최종 확인하면 재사용 규칙이 기록된다. 개발 입력만 쓴다.
const DEV = path.resolve('..', 'data', 'dirty', 'development');

test('SC07: 재사용으로 고른 묶음·매핑이 최종 확인과 함께 기록된다', async ({ page, request }) => {
  const run = await (
    await request.post('/api/runs', {
      multipart: {
        set: 'shop',
        source: `SC07-${Date.now()}`,
        files: { name: 'categories.csv', mimeType: 'text/csv', buffer: fs.readFileSync(path.join(DEV, 'categories.csv')) },
      },
    })
  ).json();
  const files = run.files.map((f: { item_id: number; mapping: object }) => ({ item_id: f.item_id, ...f.mapping }));
  await request.put(`/api/runs/${run.id}/mapping`, { data: { files } });
  await request.post(`/api/runs/${run.id}/diagnose`);

  await page.goto(`/runs/${run.id}/preview`);
  const first = page.getByTestId('reuse-group').first();
  await first.check();
  await expect(first).toBeChecked();
  await page.getByRole('button', { name: '최종 확인' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByTestId('reuse-mapping').first().check();
  await expect(dialog.getByTestId('reuse-count')).toHaveText('2');
  await dialog.getByRole('button', { name: '실행' }).click();
  await expect(page).toHaveURL(new RegExp(`/runs/${run.id}/result$`));

  const result = await (await request.get(`/api/runs/${run.id}/result`)).json();
  expect(result.reuse_rules ?? result.result?.reuse_rules).toBe(2);
});
