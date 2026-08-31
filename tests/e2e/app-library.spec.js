/**
 * App Library E2E tests.
 * Uses API to create a test user then injects token via localStorage.
 * Actual selectors verified against index.html:
 *   .app-card - tile elements with onclick="switchAgent(...)"
 *   #input - main chat textarea (inside .chat-input-wrapper)
 *   #onboard-overlay - onboarding overlay that blocks new users
 *   #ob-finish-btn - "Start using Ada →" button to dismiss onboarding
 *   #usage-btn - usage toggle button
 */
const { test, expect, request: apiRequest } = require('@playwright/test');
const { BASE_URL, createTestUser, deleteTestUser } = require('../helpers/api');

let globalToken;
let globalRequest;

test.beforeAll(async ({ playwright }) => {
  globalRequest = await playwright.request.newContext();
  const user = await createTestUser(globalRequest);
  expect(user.token, 'Test user creation should return token').toBeTruthy();
  globalToken = user.token;
});

test.afterAll(async () => {
  if (globalToken) await deleteTestUser(globalRequest, globalToken);
  await globalRequest.dispose();
});

async function openAuthenticatedApp(page) {
  await page.goto(BASE_URL);
  await page.evaluate((token) => localStorage.setItem('adado_token', token), globalToken);
  await page.reload();
  await page.waitForTimeout(1000);
}

async function dismissOnboardingIfVisible(page) {
  const overlay = page.locator('#onboard-overlay');
  const isVisible = await overlay.isVisible().catch(() => false);
  if (isVisible) {
    // Click through or dismiss onboarding
    const finishBtn = page.locator('#ob-finish-btn');
    if (await finishBtn.isVisible()) {
      await finishBtn.click();
      await page.waitForTimeout(500);
    } else {
      // Hide overlay via JS evaluation
      await page.evaluate(() => {
        const el = document.getElementById('onboard-overlay');
        if (el) el.style.display = 'none';
      });
    }
  }
}

test('app library renders tiles', async ({ page }) => {
  await openAuthenticatedApp(page);
  const tiles = page.locator('.app-card');
  await expect(tiles.first()).toBeVisible({ timeout: 10000 });
  const count = await tiles.count();
  expect(count).toBeGreaterThan(5);
});

test('tiles have visible name text', async ({ page }) => {
  await openAuthenticatedApp(page);
  const tiles = page.locator('.app-card');
  await expect(tiles.first()).toBeVisible({ timeout: 10000 });
  const count = await tiles.count();
  expect(count).toBeGreaterThan(0);
  const firstText = await tiles.first().innerText();
  expect(firstText.trim().length).toBeGreaterThan(0);
});

test('chat input is present in DOM', async ({ page }) => {
  await openAuthenticatedApp(page);
  // #input is the main chat textarea — may not be visible until onboarding dismissed
  const chatInput = page.locator('#input');
  await expect(chatInput).toBeAttached({ timeout: 8000 });
  // After dismissing onboarding it should become visible
  await dismissOnboardingIfVisible(page);
  await expect(chatInput).toBeVisible({ timeout: 5000 });
});

test('clicking an app tile calls switchAgent (no JS errors)', async ({ page }) => {
  await openAuthenticatedApp(page);
  await dismissOnboardingIfVisible(page);

  const tiles = page.locator('.app-card');
  const count = await tiles.count();
  if (count >= 2) {
    const errors = [];
    page.on('pageerror', (err) => errors.push(err.message));
    await tiles.nth(1).click({ timeout: 5000 });
    await page.waitForTimeout(500);
    expect(errors.filter(e => !e.includes('favicon'))).toHaveLength(0);
  } else {
    console.log('NOTE: Less than 2 tiles — skipping switch test');
  }
});

test('usage button is present for authenticated users', async ({ page }) => {
  await openAuthenticatedApp(page);
  await dismissOnboardingIfVisible(page);
  const usageBtn = page.locator('#usage-btn');
  // May be hidden initially but must exist in DOM
  await expect(usageBtn).toBeAttached({ timeout: 5000 });
});

test('no critical JavaScript errors on load', async ({ page }) => {
  const errors = [];
  page.on('console', (msg) => { if (msg.type() === 'error') errors.push(msg.text()); });
  page.on('pageerror', (err) => errors.push(err.message));

  await openAuthenticatedApp(page);
  await page.waitForTimeout(2000);

  const critical = errors.filter(e =>
    !e.includes('favicon') &&
    !e.includes('chrome-extension') &&
    !e.includes('Failed to load resource') &&
    !e.includes('ERR_BLOCKED')
  );
  if (critical.length > 0) console.log('JS errors:', critical);
  expect(critical).toHaveLength(0);
});
