import { expect, test, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// S2 반복 파일 처리(M6-9): 같은 출처·양식의 두 번째 파일을 화면만으로 처리한다.
// 첫 실행에서 재사용 규칙 승인 → 두 번째 업로드 → SC03 변경 확인 → SC04 재사용 매핑·영향 선택
// → SC07 미리 선택(변경 감지 컬럼은 제외) → 최종 확인 → 결과. 개발 입력과 그 변형만 쓴다.
const DEV = path.resolve('..', 'data', 'dirty', 'development');
const CUSTOMERS = fs.readFileSync(path.join(DEV, 'customers.csv'), 'utf-8');
const PHONE = 'customers.phone.pattern';
const STATUS = 'customers.customer_status.comparison_normalization';
const num = (s: string) => Number(s.replace(/[^0-9]/g, ''));

function withNewStatus(): Buffer {
  const lines = CUSTOMERS.trimEnd().split('\n');
  const col = lines[0].split(',').indexOf('회원상태');
  const first = lines[1].split(',');
  first[col] = 'DORMANT'; // 범주 새 값 → 스키마 변경(F10)
  lines[1] = first.join(',');
  return Buffer.from(lines.join('\n') + '\n', 'utf-8');
}

async function upload(page: Page, buffer: Buffer, source: string) {
  await page.goto('/upload');
  await page.setInputFiles('input[type=file]', { name: 'customers.csv', mimeType: 'text/csv', buffer });
  await page.getByPlaceholder('출처 이름').fill(source);
  await page.getByRole('button', { name: /업로드/ }).click();
}

async function diagnoseToPreview(page: Page) {
  await page.getByRole('button', { name: '진단 실행' }).click();
  await page.waitForURL(/\/diagnosis$/);
  await page.getByRole('link', { name: '변경 미리보기' }).click();
  await page.waitForURL(/\/preview$/);
  await expect(page.getByTestId('total-cells')).toBeVisible();
}

const groupRow = (page: Page, gid: string) =>
  page.getByTestId('group-row').filter({ has: page.getByRole('checkbox', { name: gid, exact: true }) });

test('S2: 같은 단위의 두 번째 파일을 재사용 규칙으로 처리한다', async ({ page, request }) => {
  const source = `S2-${Date.now()}`;

  // 1) 첫 실행: 묶음·매핑을 재사용 규칙으로 승인한다
  await upload(page, Buffer.from(CUSTOMERS, 'utf-8'), source);
  await page.waitForURL(/\/mapping$/);
  await page.getByRole('button', { name: '매핑 확정' }).click();
  await diagnoseToPreview(page);
  for (const gid of [PHONE, STATUS]) await groupRow(page, gid).getByTestId('reuse-group').check();
  await page.getByRole('button', { name: '최종 확인' }).click();
  await page.getByRole('dialog').getByTestId('reuse-mapping').first().check();
  await expect(page.getByRole('dialog').getByTestId('reuse-count')).toHaveText('3');
  await page.getByRole('button', { name: '실행', exact: true }).click();
  await page.waitForURL(/\/result$/);

  // 2) 두 번째 업로드: 매핑 전에 스키마 변경을 확인한다(SC03)
  await upload(page, withNewStatus(), source);
  await page.waitForURL(/\/drift$/);
  const runId = page.url().split('/')[4];
  await expect(page.getByTestId('drift-state').first()).toHaveText('변경 있음');
  await expect(page.getByTestId('drift-changes').first()).toContainText('범주 새 값');
  await page.getByTestId('drift-confirm').click();
  await page.waitForURL(/\/mapping$/);

  // 3) 재사용 매핑이 자동 제안되고, 매핑을 바꾸면 재사용 영향을 고른다(SC04, F03-6)
  await expect(page.getByText('재사용 규칙').first()).toBeVisible();
  await page.locator('tr', { hasText: '이메일' }).first().locator('button').first().click();
  await page.getByRole('button', { name: '매핑 확정' }).click();
  const panel = page.getByTestId('reuse-impact');
  await expect(panel).toBeVisible();
  await panel.getByRole('button', { name: '이번만 새 매핑' }).click();
  await panel.getByRole('button', { name: '매핑 확정' }).click();
  await expect(panel).toBeHidden();
  await expect(page.getByRole('button', { name: '진단 실행' })).toBeEnabled();

  // 4) 미리 선택: 규칙과 맞는 묶음만, 변경이 감지된 회원상태는 일반 검토(SC07, AC-09-2)
  await diagnoseToPreview(page);
  await expect(groupRow(page, PHONE).getByTestId('group-reuse')).toBeVisible();
  await expect(groupRow(page, PHONE).getByRole('checkbox', { name: PHONE, exact: true })).toBeChecked();
  await expect(groupRow(page, STATUS).getByTestId('group-reuse')).toHaveCount(0);
  await expect(groupRow(page, STATUS).getByRole('checkbox', { name: STATUS, exact: true })).not.toBeChecked();
  const applied = num(await page.getByTestId('total-cells').innerText());
  expect(applied).toBeGreaterThan(0);

  // 5) 미리 선택도 최종 확인을 거쳐야 적용된다(AC-09-3)
  expect((await (await request.get(`/api/runs/${runId}`)).json()).status).toBe('awaiting_approval');
  await page.getByRole('button', { name: '최종 확인' }).click();
  await expect(page.getByTestId('confirm-apply')).toHaveText(applied.toLocaleString('en-US'));
  await page.getByRole('button', { name: '실행', exact: true }).click();
  await page.waitForURL(/\/result$/);
  const changes = await (await request.get(`/api/runs/${runId}/changes?limit=1`)).json();
  expect(changes.total).toBe(applied);

  // "이번만 새 매핑"은 규칙을 바꾸지 않는다
  const units = await (await request.get('/api/reuse-rules')).json();
  const unit = units.find((u: { source: { name: string } }) => u.source.name === source);
  const mapping = unit.rules.filter((r: { kind: string }) => r.kind === 'mapping');
  expect(mapping.map((r: { version: number; state: string }) => [r.version, r.state])).toEqual([[1, 'effective']]);
});
