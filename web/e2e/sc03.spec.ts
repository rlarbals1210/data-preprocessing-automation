import { expect, test, type APIRequestContext } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// SC03 스키마 변경 확인(M6-4): 같은 출처에 컬럼을 하나 빼고 하나 더한 파일을 올리면 매핑 전에 변경을 확인한다. 개발 입력만 쓴다.
const DEV = path.resolve('..', 'data', 'dirty', 'development');
const CATEGORIES = fs.readFileSync(path.join(DEV, 'categories.csv'), 'utf-8').replace(/^﻿/, '');

function reshaped(): Buffer {
  const lines = CATEGORIES.trimEnd().split('\n').map((l) => l.split(','));
  const drop = lines[0].indexOf('상위분류번호');
  const out = lines.map((r, i) => [...r.filter((_, j) => j !== drop), i === 0 ? '비고' : 'x'].join(','));
  return Buffer.from(out.join('\n') + '\n', 'utf-8');
}

async function upload(request: APIRequestContext, source: string, buffer: Buffer) {
  return (
    await request.post('/api/runs', {
      multipart: { set: 'shop', source, files: { name: 'categories.csv', mimeType: 'text/csv', buffer } },
    })
  ).json();
}

async function publish(request: APIRequestContext, source: string) {
  const run = await upload(request, source, Buffer.from(CATEGORIES, 'utf-8'));
  const files = run.files.map((f: { item_id: number; mapping: object }) => ({ item_id: f.item_id, ...f.mapping }));
  await request.put(`/api/runs/${run.id}/mapping`, { data: { files } });
  await request.post(`/api/runs/${run.id}/diagnose`);
  const p = await (await request.get(`/api/runs/${run.id}/preview`)).json();
  await request.post(`/api/runs/${run.id}/execute`, { data: { approval: p.approval } });
}

test('SC03: 같은 단위의 변경은 매핑 전에 확인한다', async ({ page, request }) => {
  const source = `SC03-${Date.now()}`;
  await publish(request, source);
  const run = await upload(request, source, reshaped());

  await page.goto(`/runs/${run.id}`);
  await expect(page).toHaveURL(new RegExp(`/runs/${run.id}/drift$`));
  const item = page.getByTestId(`drift-item-${run.files[0].item_id}`);
  await expect(item.getByTestId('drift-state')).toHaveText('변경 있음');
  const changes = item.getByTestId('drift-changes');
  await expect(changes).toContainText('컬럼 삭제');
  await expect(changes).toContainText('상위분류번호');
  await expect(changes).toContainText('컬럼 추가');
  await expect(changes).toContainText('비고');
  await page.screenshot({ path: process.env.SC03_SHOT ?? 'test-results/sc03.png', fullPage: true });

  await page.goto(`/runs/${run.id}/mapping`);
  await expect(page).toHaveURL(new RegExp(`/runs/${run.id}/drift$`)); // 확인 전에는 매핑으로 못 감
  await page.getByTestId('drift-confirm').click();
  await expect(page).toHaveURL(new RegExp(`/runs/${run.id}/mapping$`));
  const d = await (await request.get(`/api/runs/${run.id}/drift`)).json();
  expect(d.confirmed).toBe(true);
});
