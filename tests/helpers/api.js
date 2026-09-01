/**
 * Shared helpers for AdaDo API tests.
 * Creates a unique test user per run and cleans up afterward.
 */

const BASE_URL = process.env.BASE_URL || 'https://adadoai.com';

function testEmail() {
  const ts = Date.now();
  return `test-playwright-${ts}@example.com`;
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

async function createTestUser(request) {
  const email = testEmail();
  const password = 'PlaywrightTest1!';
  const name = 'Playwright Bot';
  const r = await apiPost(request, '/api/auth/signup', { email, password, name });
  const body = await r.json();
  return { email, password, name, token: body.token, status: r.status() };
}

async function loginTestUser(request, email, password) {
  const r = await apiPost(request, '/api/auth/login', { email, password });
  const body = await r.json();
  return { token: body.token, status: r.status() };
}

async function deleteTestUser(request, token) {
  await apiDelete(request, '/api/auth/me', token);
}

module.exports = { BASE_URL, apiPost, apiGet, apiDelete, createTestUser, loginTestUser, deleteTestUser };
