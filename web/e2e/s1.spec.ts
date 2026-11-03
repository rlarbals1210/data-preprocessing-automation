import { expect, test, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// S1 처음 받은 파일 정리(M3-5): 코드 없이 화면만으로 업로드 → 매핑 확정 → 진단 → 선택 → 최종 확인 → 결과·다운로드.
// 개발 입력만 쓴다(정답 미사용).
const DEV = path.resolve('..', 'data', 'dirty', 'development');
const num = (s: string) => Number(s.replace(/[^0-9]/g, ''));

async function upload(page: Page, names: string[], source = '판매팀') {
  await page.goto('/upload');
  await page.setInputFiles(
    'input[type=file]',
    names.map((n) => ({ name: n, mimeType: 'text/csv', buffer: fs.readFileSync(path.join(DEV, n)) })),
  );
  await page.getByPlaceholder('출처 이름').fill(source);
  await page.getByRole('button', { name: /업로드/ }).click();
  await page.waitForURL(/\/mapping$/);
}

async function toPreview(page: Page) {
  await page.getByRole('button', { name: '매핑 확정' }).click();
  await expect(page.getByRole('button', { name: '진단 실행' })).toBeEnabled();
  await page.getByRole('button', { name: '진단 실행' }).click();
  await page.waitForURL(/\/diagnosis$/);
  await page.getByRole('link', { name: '변경 미리보기' }).click();
  await page.waitForURL(/\/preview$/);
  await expect(page.getByTestId('total-cells')).toBeVisible();
}

test('S1: 화면만으로 정리하고 내려받는다', async ({ page, request }) => {
  await upload(page, ['customers.csv', 'orders.csv']);
  await expect(page.getByText('customers.csv', { exact: true })).toBeVisible();
  // 진단 전에는 진단 버튼이 꺼져 있다(AC-03-3: 매핑 확정 전)
  await expect(page.getByRole('button', { name: '진단 실행' })).toBeDisabled();
  await page.getByRole('button', { name: '매핑 확정' }).click();
  await page.getByRole('button', { name: '진단 실행' }).click();
  await page.waitForURL(/\/diagnosis$/);
  await expect(page.getByText('전체 검사')).toBeVisible(); // orders는 customers만 참조
  await expect(page.getByTestId('duplicate-group').first()).toBeVisible();

  // 원본 위치의 원문 보기(AC-04-4)
  await page.getByRole('button', { name: '원문 보기' }).first().click();
  await expect(page.getByTestId('source-row')).toBeVisible();
  await page.keyboard.press('Escape');

  await page.getByRole('link', { name: '변경 미리보기' }).click();
  await page.waitForURL(/\/preview$/);
  const total = num(await page.getByTestId('total-cells').innerText());

  // 묶음 하나를 빼고, 다른 묶음에서 셀 하나를 뺀다(AC-06-1)
  const dropped = page.getByRole('checkbox', { name: 'orders.order_status.comparison_normalization' });
  const droppedRow = page.getByTestId('group-row').filter({ has: dropped });
  const droppedCells = num(await droppedRow.locator('td').nth(5).innerText());
  await dropped.click(); // 서버가 검증한 선택으로 화면이 갱신된다
  await expect(page.getByTestId('total-cells')).toHaveText((total - droppedCells).toLocaleString('en-US'));
  await page.getByRole('button', { name: 'customers.phone.pattern 셀 보기' }).click();
  await page.getByTestId('group-cell').first().getByRole('checkbox').click();
  const applied = total - droppedCells - 1;
  await expect(page.getByTestId('total-cells')).toHaveText(applied.toLocaleString('en-US'));

  await page.getByRole('button', { name: '최종 확인' }).click();
  await expect(page.getByTestId('confirm-apply')).toHaveText(applied.toLocaleString('en-US'));
  await page.getByRole('button', { name: '실행', exact: true }).click();
  await page.waitForURL(/\/result$/);

  // 결과: 적용 셀 수 = 변경 내역 고유 셀 수(AC-06-5), 남은 문제 목록, 내려받기
  await expect(page.getByText('완료 · 문제 남음').first()).toBeVisible();
  await expect(page.getByText('변경 내역').first()).toBeVisible();
  await expect(page.getByTestId('remaining')).toBeVisible();
  const runId = page.url().split('/')[4];
  const changes = await (await request.get(`/api/runs/${runId}/changes?limit=1`)).json();
  expect(changes.total).toBe(applied);
  const csv = await request.get(`/api/runs/${runId}/files/customers.csv`);
  expect(csv.status()).toBe(200);
  expect((await csv.text()).split('\n')[0].startsWith('customer_id,')).toBeTruthy();
  const json = await (await request.get(`/api/runs/${runId}/files/changes.json`)).json();
  expect(json.length).toBe(applied);
  await expect(page.getByRole('link', { name: '변경 내역 (JSON, 논리 null 구분)' })).toBeVisible();
});

test('매핑을 바꾸면 이전 진단·미리보기 승인이 무효가 되고 다시 진단해야 한다', async ({ page }) => {
  await upload(page, ['customers.csv']);
  await toPreview(page);
  await page.getByText('매핑 확인').click();
  await page.waitForURL(/\/mapping$/);
  await page.locator('tr', { hasText: '이메일' }).first().locator('button').first().click();
  await page.getByRole('button', { name: '매핑 확정' }).click();
  await expect(page.getByRole('dialog')).toContainText('무효');
  await page.getByRole('dialog').getByRole('button', { name: '확정' }).click();
  await expect(page.getByTestId('invalidated')).toBeVisible();
  await page.getByRole('button', { name: /승인 기록/ }).click();
  await expect(page.getByTestId('approvals')).toContainText('무효 · 매핑 변경');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '진단 실행' }).click();
  await page.waitForURL(/\/diagnosis$/);
  await expect(page.getByTestId('invalidated')).toHaveCount(0);
});

test('미리보기를 연 뒤 선택이 바뀌면 그 화면의 승인으로는 실행되지 않는다', async ({ page, request }) => {
  await upload(page, ['categories.csv']);
  await toPreview(page);
  const runId = page.url().split('/')[4];
  // 다른 곳(다른 탭)에서 선택을 바꾼다
  const r = await request.put(`/api/runs/${runId}/selection`, { data: { approved_groups: [], excluded_cells: [] } });
  expect(r.status()).toBe(200);
  await page.getByRole('button', { name: '최종 확인' }).click();
  await page.getByRole('button', { name: '실행', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('적용 선택');
  await expect(page).toHaveURL(/\/preview$/);
});
