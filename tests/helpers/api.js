/**
 * Shared helpers for AdaDo API tests.
 * Creates a unique test user per run and cleans up afterward.
 */

const BASE_URL = process.env.BASE_URL || 'https://adadoai.com';
const TEST_INVITE_CODE = process.env.TEST_INVITE_CODE || 'CI-TEST-AUTORUN';

function testEmail() {
  const ts = Date.now();
  return `test-playwright-${ts}@example.com`;
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function apiPost(request, path, body, token) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const r = await request.post(`${BASE_URL}${path}`, { data: body, headers });
  return r;
}

async function apiGet(request, path, token) {
  const headers = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return request.get(`${BASE_URL}${path}`, { headers });
}

async function apiDelete(request, path, token) {
  const headers = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return request.delete(`${BASE_URL}${path}`, { headers });
}

async function safeJson(r) {
  const ct = r.headers()['content-type'] || '';
  if (!ct.includes('application/json')) return {};
  try { return await r.json(); } catch { return {}; }
}

async function createTestUser(request, retries = 3) {
  const email = testEmail();
  const password = 'PlaywrightTest1!';
  const name = 'Playwright Bot';
  for (let i = 0; i < retries; i++) {
    const r = await apiPost(request, '/api/auth/signup', {
      email, password, name, invite_code: TEST_INVITE_CODE, terms_accepted: true,
    });
    if (r.status() === 429) {
      await sleep(15000);
      continue;
    }
    const body = await safeJson(r);
    const token = body.token || body.access_token;
    return { email, password, name, token, status: r.status() };
  }
  return { email, password, name, token: null, status: 429 };
}

async function loginTestUser(request, email, password, retries = 3) {
  for (let i = 0; i < retries; i++) {
    const r = await apiPost(request, '/api/auth/login', { email, password });
    if (r.status() === 429) {
      await sleep(15000);
      continue;
    }
    const body = await safeJson(r);
    const token = body.token || body.access_token;
    return { token, status: r.status() };
  }
  return { token: null, status: 429 };
}

async function deleteTestUser(request, token) {
  await apiDelete(request, '/api/auth/me', token);
}

module.exports = { BASE_URL, apiPost, apiGet, apiDelete, createTestUser, loginTestUser, deleteTestUser };
