const { test, expect } = require('@playwright/test');
const BASE_URL = 'https://adadoai.com';

test('test apps endpoint', async ({ request }) => {
  // Create user
  const ts = Date.now();
  const email = `test-pw-${ts}@test.com`;
  const signup = await request.post(`${BASE_URL}/api/auth/signup`, {
    data: { email, password: 'Test123!', name: 'T', invite_code: 'CI-TEST-AUTORUN' },
    headers: { 'Content-Type': 'application/json' }
  });
  console.log('Signup status:', signup.status());
  const body = await signup.json();
  console.log('Signup body:', JSON.stringify(body).substring(0, 200));
  const token = body.token || body.access_token;
  console.log('Token:', token ? token.substring(0, 40) + '...' : 'NONE');
  
  // Test /api/auth/me
  const meR = await request.get(`${BASE_URL}/api/auth/me`, {
    headers: { 'Authorization': `Bearer ${token}` }
  });
  console.log('me status:', meR.status());
  const meBody = await meR.json();
  console.log('me body:', JSON.stringify(meBody).substring(0, 200));
  
  // Test /api/apps
  const appsR = await request.get(`${BASE_URL}/api/apps`, {
    headers: { 'Authorization': `Bearer ${token}` }
  });
  console.log('apps status:', appsR.status());
  const appsBody = await appsR.text();
  console.log('apps body:', appsBody.substring(0, 200));
});
