import { expect, test } from '@playwright/test';

test('builds and advances a route on the supplied plan', async ({ page }, testInfo) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: /Find your way/i })).toBeVisible();

  const selects = page.locator('select');
  await selects.nth(0).selectOption('registration');
  await selects.nth(1).selectOption('leap-arena');
  await page.getByRole('button', { name: 'Start voice navigation' }).click();

  await expect(page.locator('.route-line')).toBeVisible();
  await expect(page.locator('.route-summary')).toContainText('LEAP Arena');
  await expect(page.locator('.current-instruction')).toContainText(/Start along/i);

  await page.getByRole('button', { name: 'Next step' }).click();
  await expect(page.locator('.current-instruction')).toContainText('STEP 2');

  await page.screenshot({ path: testInfo.outputPath('route.png'), fullPage: true });
});

test('switches the route planner and guidance to Arabic', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'ع' }).click();
  await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');

  const selects = page.locator('select');
  await selects.nth(0).selectOption('registration');
  await selects.nth(1).selectOption('food-zone');
  await page.getByRole('button', { name: 'ابدأ الملاحة الصوتية' }).click();

  await expect(page.locator('.route-line')).toBeVisible();
  await expect(page.locator('.route-summary')).toContainText('منطقة الطعام');
  await expect(page.locator('.current-instruction')).toContainText('الخطوة');
});
