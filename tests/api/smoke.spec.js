/**
 * API smoke tests — no browser, pure HTTP.
 * These run fast (< 5s) and can catch backend regressions immediately.
 */
const { test, expect } = require('@playwright/test');
const { BASE_URL, apiGet, apiPost, createTestUser, loginTestUser, deleteTestUser } = require('../helpers/api');

// Shared user for this test file's session
let sharedToken;
let sharedEmail;
let sharedPassword;

test.beforeAll(async ({ request }) => {
  const user = await createTestUser(request);
  expect(user.status, 'Signup should return 200').toBe(200);
  expect(user.token, 'Signup should return a JWT').toBeTruthy();
  sharedToken = user.token;
  sharedEmail = user.email;
  sharedPassword = user.password;
});

test.afterAll(async ({ request }) => {
  if (sharedToken) await deleteTestUser(request, sharedToken);
});

test('health endpoint returns ok', async ({ request }) => {
  const r = await request.get(`${BASE_URL}/api/health`);
  expect(r.status()).toBe(200);
  const body = await r.json();
  expect(body.status).toBe('ok');
});

test('app library returns 20+ apps', async ({ request }) => {
  const r = await apiGet(request, '/api/apps', sharedToken);
  expect(r.status()).toBe(200);
  const apps = await r.json();
  expect(Array.isArray(apps)).toBeTruthy();
  expect(apps.length).toBeGreaterThanOrEqual(20);
  // Every app must have id and name
  for (const app of apps.slice(0, 5)) {
    expect(app.id).toBeTruthy();
    expect(app.name).toBeTruthy();
  }
});

test('signup with duplicate email returns error', async ({ request }) => {
  const r = await apiPost(request, '/api/auth/signup', {
    email: sharedEmail,
    password: sharedPassword,
    name: 'Duplicate',
  });
  expect(r.status()).toBeGreaterThanOrEqual(400);
});

test('login with correct credentials returns token', async ({ request }) => {
  const result = await loginTestUser(request, sharedEmail, sharedPassword);
  expect(result.status).toBe(200);
  expect(result.token).toBeTruthy();
  expect(result.token.split('.').length).toBe(3); // valid JWT format
});

test('login with wrong password returns 401', async ({ request }) => {
  const r = await apiPost(request, '/api/auth/login', {
    email: sharedEmail,
    password: 'WrongPassword999!',
  });
  expect(r.status()).toBe(401);
});

test('/api/auth/me returns user data with valid token', async ({ request }) => {
  const r = await apiGet(request, '/api/auth/me', sharedToken);
  expect(r.status()).toBe(200);
  const user = await r.json();
  expect(user.email).toBe(sharedEmail);
  expect(user.id).toBeTruthy();
  expect(user.tier).toBeTruthy();
});

test('/api/auth/me returns 401 with no token', async ({ request }) => {
  const r = await request.get(`${BASE_URL}/api/auth/me`);
  expect(r.status()).toBe(401);
});

test('/api/usage/quota returns quota structure', async ({ request }) => {
  const r = await apiGet(request, '/api/usage/quota', sharedToken);
  expect(r.status()).toBe(200);
  const q = await r.json();
  expect(q.plan).toBeTruthy();
  expect(typeof q.daily_limit).toBe('number');
  expect(typeof q.used_today).toBe('number');
  expect(typeof q.remaining_today).toBe('number');
  expect(typeof q.quota_exceeded).toBe('boolean');
  expect(typeof q.has_byok).toBe('boolean');
});

test('/api/settings/byok rejects invalid key format', async ({ request }) => {
  const r = await apiPost(request, '/api/settings/byok', { api_key: 'not-a-real-key' }, sharedToken);
  expect(r.status()).toBeGreaterThanOrEqual(400);
});

test('static index.html loads', async ({ request }) => {
  const r = await request.get(`${BASE_URL}/`);
  expect(r.status()).toBe(200);
  const body = await r.text();
  expect(body).toContain('AdaDo');
});

test('app YAML files have short_description field (sample check)', async ({ request }) => {
  const r = await apiGet(request, '/api/apps', sharedToken);
  const apps = await r.json();
  // At least half the apps should have short_description or description
  const withDesc = apps.filter(a => a.short_description || a.description);
  expect(withDesc.length).toBeGreaterThan(apps.length / 2);
});
