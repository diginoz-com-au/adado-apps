/**
 * E2E auth tests — signup and login via the browser UI.
 * Selectors verified against actual index.html structure:
 *   #auth-btn → showAuth() → #auth-modal (display:flex)
 *   #email-input, #pw-input, #name-input (inside auth modal)
 *   #auth-switch-link → toggleAuth() (switches between sign-in and sign-up)
 *   #auth-submit, #auth-title
 */
const { test, expect } = require('@playwright/test');
const { BASE_URL } = require('../helpers/api');

test.describe('Home page', () => {
  test('loads with correct title', async ({ page }) => {
    await page.goto(BASE_URL);
    await expect(page).toHaveTitle(/AdaDo/i);
  });

  test('Sign in button is present in nav', async ({ page }) => {
    await page.goto(BASE_URL);
    const authBtn = page.locator('#auth-btn');
    await expect(authBtn).toBeVisible();
    await expect(authBtn).toHaveText(/sign in/i);
  });

  test('Sign in button opens auth modal', async ({ page }) => {
    await page.goto(BASE_URL);
    await page.locator('#auth-btn').click();
    const modal = page.locator('#auth-modal');
    await expect(modal).toBeVisible({ timeout: 5000 });
    await expect(page.locator('#email-input')).toBeVisible();
  });
});

test.describe('Signup flow', () => {
  let testEmail;
  const testPassword = 'TestPW1234!';

  test.beforeEach(() => {
    testEmail = `pw-e2e-${Date.now()}@adado.test`;
  });

  test('can toggle from sign-in to sign-up mode', async ({ page }) => {
    await page.goto(BASE_URL);
    await page.locator('#auth-btn').click();
    await expect(page.locator('#auth-modal')).toBeVisible({ timeout: 5000 });

    // Should be in sign-in mode by default
    await expect(page.locator('#auth-title')).toHaveText(/sign in/i);

    // Click "Create one" to switch to signup
    await page.locator('#auth-switch-link').click();
    await expect(page.locator('#auth-title')).toHaveText(/create account|sign up/i, { timeout: 3000 });
    await expect(page.locator('#name-input')).toBeVisible();
  });

  test('signup form accepts input', async ({ page }) => {
    await page.goto(BASE_URL);
    await page.locator('#auth-btn').click();
    await expect(page.locator('#auth-modal')).toBeVisible({ timeout: 5000 });
    await page.locator('#auth-switch-link').click(); // switch to signup

    await page.locator('#name-input').fill('E2E Test User');
    await page.locator('#email-input').fill(testEmail);
    await page.locator('#pw-input').fill(testPassword);

    // Fields should have the values
    await expect(page.locator('#email-input')).toHaveValue(testEmail);
    await expect(page.locator('#pw-input')).toHaveValue(testPassword);
  });

  test('successful signup dismisses modal and shows app', async ({ page }) => {
    await page.goto(BASE_URL);
    await page.locator('#auth-btn').click();
    await expect(page.locator('#auth-modal')).toBeVisible({ timeout: 5000 });
    await page.locator('#auth-switch-link').click();

    await page.locator('#name-input').fill('E2E Signup Test');
    await page.locator('#email-input').fill(testEmail);
    await page.locator('#pw-input').fill(testPassword);
    await page.locator('#auth-submit').click();

    // Modal should close after successful signup
    await expect(page.locator('#auth-modal')).toBeHidden({ timeout: 8000 });

    // App interface should appear (onboarding overlay or interests)
    const appShown = page.locator('#onboard-overlay, #interests-overlay, #input').first();
    await expect(appShown).toBeVisible({ timeout: 8000 });
  });
});

test.describe('Login flow', () => {
  test('sign-in modal has email and password fields', async ({ page }) => {
    await page.goto(BASE_URL);
    await page.locator('#auth-btn').click();
    await expect(page.locator('#auth-modal')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('#email-input')).toBeVisible();
    await expect(page.locator('#pw-input')).toBeVisible();
    await expect(page.locator('#auth-submit')).toBeVisible();
  });

  test('wrong credentials shows error in auth-err element', async ({ page }) => {
    await page.goto(BASE_URL);
    await page.locator('#auth-btn').click();
    await expect(page.locator('#auth-modal')).toBeVisible({ timeout: 5000 });

    await page.locator('#email-input').fill('doesnotexist@adado.test');
    await page.locator('#pw-input').fill('WrongPassword!');
    await page.locator('#auth-submit').click();

    // Error message should appear (not a 500 crash)
    const errEl = page.locator('#auth-err');
    await expect(errEl).toBeVisible({ timeout: 5000 });
    const errText = await errEl.innerText();
    expect(errText.length).toBeGreaterThan(0);
    expect(errText).not.toContain('500');
  });
});
