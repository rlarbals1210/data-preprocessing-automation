import { expect, test, type APIRequestContext } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// SC04 재사용 매핑(M6-6a): 재사용 매핑 규칙이 있는 같은 양식은 '재사용 규칙'으로 제안되고,
// 헤더를 영문으로 바꾼 파일은 매핑 확정 때 재사용 규칙 영향을 고른다(AC-10-2). 개발 입력만 쓴다.
const DEV = path.resolve('..', 'data', 'dirty', 'development');
const CATEGORIES = fs.readFileSync(path.join(DEV, 'categories.csv'), 'utf-8').replace(/^﻿/, '');
const ENGLISH: Record<string, string> = {
  분류번호: 'category_id',
  상위분류번호: 'parent_category_id',
  분류명: 'category_name',
  사용여부: 'is_active',
};

async function upload(request: APIRequestContext, source: string, text: string) {
  return (
    await request.post('/api/runs', {
      multipart: { set: 'shop', source, files: { name: 'categories.csv', mimeType: 'text/csv', buffer: Buffer.from(text, 'utf-8') } },
    })
  ).json();
}

async function seed(request: APIRequestContext, source: string) {
  const run = await upload(request, source, CATEGORIES);
  const files = run.files.map((f: { item_id: number; mapping: object }) => ({ item_id: f.item_id, ...f.mapping }));
  await request.put(`/api/runs/${run.id}/mapping`, { data: { files } });
  await request.post(`/api/runs/${run.id}/diagnose`);
  const p = await (await request.get(`/api/runs/${run.id}/preview`)).json();
  await request.post(`/api/runs/${run.id}/execute`, {
    data: { approval: p.approval, reuse: { groups: [], mappings: [run.files[0].item_id] } },
  });
}

test('SC04: 같은 양식은 재사용 규칙으로 제안되고, 헤더가 바뀐 양식은 재사용 영향을 고른다', async ({ page, request }) => {
  const source = `SC04-${Date.now()}`;
  await seed(request, source);

  const same = await upload(request, source, CATEGORIES);
  await page.goto(`/runs/${same.id}/mapping`);
  await expect(page.getByText('재사용 규칙').first()).toBeVisible();

  const [head, ...rest] = CATEGORIES.trimEnd().split('\n');
  const english = [head.split(',').map((h) => ENGLISH[h]).join(','), ...rest].join('\n') + '\n';
  const run = await upload(request, source, english);
  await page.goto(`/runs/${run.id}/mapping`);
  await page.getByRole('button', { name: '매핑 확정' }).click();
  const panel = page.getByTestId('reuse-impact');
  await expect(panel).toContainText('헤더 구성 변경');
  await panel.getByRole('button', { name: '새 규칙 버전 승인' }).click();
  await panel.getByRole('button', { name: '매핑 확정' }).click();
  await expect(panel).toBeHidden();
  const after = await (await request.get(`/api/runs/${run.id}`)).json();
  expect(after.files[0].confirmed).toBe(true);
});
