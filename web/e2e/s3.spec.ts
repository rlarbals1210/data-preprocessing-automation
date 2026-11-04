import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// S3 여러 담당자 파일 통합(M4-7): CSV 1개 + xlsx 시트 2개를 한 테이블로 합치고, 주문·주문 상세로 정합성을 본다.
// fixture는 tools/build_e2e_fixtures.py가 개발 입력에서 만든다(정답 미사용).
const DEV = path.resolve('..', 'data', 'dirty', 'development');
const FIX = path.resolve('e2e', 'fixtures');
const file = (dir: string, name: string, mime = 'text/csv') => ({
  name,
  mimeType: mime,
  buffer: fs.readFileSync(path.join(dir, name)),
});
const XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
const customerRows = fs.readFileSync(path.join(DEV, 'customers.csv'), 'utf-8').trim().split('\n').length - 1;

test('S3: CSV·엑셀 시트를 합치고 정합성을 검토한 뒤 발행한다', async ({ page, request }) => {
  await page.goto('/upload');
  await page.setInputFiles('input[type=file]', [
    file(FIX, '고객-앞.csv'),
    file(FIX, '고객-뒤.xlsx', XLSX),
    file(DEV, 'orders.csv'),
    file(DEV, 'order_items.csv'),
  ]);
  await page.getByPlaceholder('출처 이름').fill('여러 담당자');
  await page.getByRole('button', { name: /업로드/ }).click();
  await page.waitForURL(/\/mapping$/);

  // 수식이 든 메모 시트는 좌표·이유와 함께 빠진다(AC-02-2), 고객 3개는 한 테이블로 합친다
  await expect(page.getByTestId('read-error')).toContainText('B2: 수식');
  await expect(page.getByText('3개 파일·시트를 합침').first()).toBeVisible();
  await page.getByRole('button', { name: '매핑 확정' }).click();
  await page.getByRole('button', { name: '진단 실행' }).click();
  await page.waitForURL(/\/diagnosis$/);
  const runId = page.url().split('/')[4];
  const diag = await (await request.get(`/api/runs/${runId}/diagnosis`)).json();
  const customers = diag.tables.find((t: { table: string }) => t.table === 'customers');
  expect(customers.rows).toBe(customerRows); // AC-02-1 행 수 = 합계

  await page.getByRole('link', { name: '정합성 검토' }).click();
  await page.waitForURL(/\/integrity$/);
  await expect(page.getByTestId('integrity-rules')).toContainText('R01');
  await page.getByText('원문 기준').click();
  await expect(page.getByTestId('integrity-rules')).toContainText('해석 불가');
  await page.getByText('가상 기준').click();
  await expect(page.getByTestId('integrity-violations')).toBeVisible();
  await page.getByText('승인 기준').click();
  await expect(page.getByTestId('integrity-total')).toBeVisible();

  await page.getByText('미리보기·승인').click();
  await page.waitForURL(/\/preview$/);
  await page.getByRole('button', { name: '최종 확인' }).click();
  await page.getByRole('button', { name: '실행', exact: true }).click();
  await page.waitForURL(/\/result$/);
  await expect(page.getByText('완료 · 문제 남음').first()).toBeVisible();
  await expect(page.getByTestId('integrity-after')).toBeVisible();

  const csv = await (await request.get(`/api/runs/${runId}/files/customers.csv`)).text();
  expect(csv.trim().split('\n').length - 1).toBe(customerRows);
  const changes = await (await request.get(`/api/runs/${runId}/changes?limit=500`)).json();
  const sheets = new Set(changes.items.filter((c: { source: { sheet?: string } }) => c.source.sheet).map(
    (c: { source: { sheet: string } }) => c.source.sheet,
  ));
  expect([...sheets].sort()).toEqual(['1월', '2월']);
});
