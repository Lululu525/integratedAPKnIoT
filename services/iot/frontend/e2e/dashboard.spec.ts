import { expect, test, type Page } from '@playwright/test';
import { buildImage } from './image';

// A fresh account per test. Registration is open, so the suite makes its own
// rather than sharing the seeded one: the point of most of these is what an
// account that owns nothing sees, and a shared account owns whatever the last
// test left behind.
function uniqueEmail() {
  return `e2e-${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
}

const PASSWORD = 'e2e-password';

async function register(page: Page, email: string) {
  await page.goto('/register');
  await page.locator('input[autocomplete="username"]').fill(email);
  await page.locator('input[autocomplete="new-password"]').fill(PASSWORD);
  await page.getByRole('button', { name: '建立帳號' }).click();
  // Straight into the dashboard: the account is usable the moment it exists.
  await expect(page).toHaveURL('/');
}

test('a new account signs up and lands on an empty fleet', async ({ page }) => {
  await register(page, uniqueEmail());

  // The seeded account has published firmware and this one must not see it.
  await expect(page.locator('.fw-group-card')).toHaveCount(0);
  await page.goto('/devices');
  await expect(page.getByText('還沒有註冊任何裝置')).toBeVisible();
});

test('a new account cannot publish until it sets a public key', async ({ page }) => {
  // The server holds no private key, so there is nothing an upload could be
  // verified against. Saying so up front beats filling the form in for a 400.
  await register(page, uniqueEmail());

  await expect(page.getByText('尚未設定')).toBeVisible();
  await expect(page.getByRole('button', { name: '要先設定簽章公鑰' })).toBeDisabled();
});

test('registering a device shows its secret once and lists it', async ({ page }) => {
  await register(page, uniqueEmail());
  await page.goto('/devices');

  await page.getByPlaceholder('裝置型號，例如 ESP32').fill('ESP32');
  await page.getByRole('button', { name: '註冊', exact: true }).click();

  // Both values, in the shape they go into config.json. This is the only time
  // the secret exists anywhere but that unit's flash.
  const block = page.locator('.dev-secret-block');
  await expect(block).toContainText('device_id');
  await expect(block).toContainText('device_secret');

  await expect(page.locator('.data-table tbody tr')).toHaveCount(1);
  // Never checked in, so its state is unknown rather than offline. By the badge
  // and not by the text: the version cell reads 未知 for the same reason.
  await expect(page.locator('.data-table tbody tr .badge-warning')).toHaveText('未知');
});

test('a registered device can be disabled and enabled again', async ({ page }) => {
  await register(page, uniqueEmail());
  await page.goto('/devices');
  await page.getByPlaceholder('裝置型號，例如 ESP32').fill('ESP32');
  await page.getByRole('button', { name: '註冊', exact: true }).click();
  await expect(page.locator('.data-table tbody tr')).toHaveCount(1);

  await page.getByRole('button', { name: '停用' }).click();

  // Disabled outranks online state: whatever the clock says, the server is
  // answering that unit 401 on its next poll.
  await expect(page.getByText('已停用')).toBeVisible();
  await page.getByRole('button', { name: '重新啟用' }).click();
  await expect(page.getByText('已停用')).toHaveCount(0);
});

test('signing out and back in keeps the account it belongs to', async ({ page }) => {
  const email = uniqueEmail();
  await register(page, email);

  await page.getByRole('button', { name: '登出' }).click();
  await expect(page).toHaveURL('/login');

  await page.locator('input[autocomplete="username"]').fill(email);
  await page.locator('input[autocomplete="current-password"]').fill(PASSWORD);
  await page.getByRole('button', { name: '登入', exact: true }).click();

  await expect(page).toHaveURL('/');
  await expect(page.getByText(email)).toBeVisible();
});

test('three parallel requests past the renew margin spend one refresh handle', async ({ page }) => {
  // The device page fires three authFetch calls at once. Handles are single
  // use, so without the single-flight guard in AuthProvider each would spend
  // one, two would come back 401 and the session would be dropped.
  await register(page, uniqueEmail());

  await page.evaluate(() => {
    const stored = sessionStorage.getItem('ota.session');
    if (!stored) throw new Error('no session to age');
    const session = JSON.parse(stored);
    session.expiresAt = Date.now() - 1000;
    sessionStorage.setItem('ota.session', JSON.stringify(session));
  });

  let refreshes = 0;
  page.on('request', request => {
    if (request.url().includes('/api/auth/refresh')) refreshes += 1;
  });

  await page.goto('/devices');
  await expect(page.getByText('還沒有註冊任何裝置')).toBeVisible();

  // Still signed in, and one renewal did it. Three is the failure this exists
  // to prevent, and it looks identical on screen right up until the logout.
  await expect(page).toHaveURL('/devices');
  expect(refreshes).toBe(1);
});

test('an account with only a browser generates a key, signs, and publishes', async ({ page }) => {
  // The whole point of the browser signing path. Nothing in this test touches
  // a terminal, a checkout, or the seeded key pair: the account is created,
  // the key pair is generated in the page, the private half comes back as a
  // download, and the signature is computed from it in the same tab.
  await register(page, uniqueEmail());

  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '在瀏覽器產生金鑰' }).click();
  const privateKey = await (await download).path();

  await expect(page.locator('textarea.key-input')).toHaveValue(/^-----BEGIN PUBLIC KEY-----/);
  await page.getByRole('button', { name: '設定公鑰' }).click();
  await expect(page.getByText('已設定')).toBeVisible();

  const model = `E2E-BROWSER-${process.pid}`;
  await page.locator('input[name="firmware"]').setInputFiles({
    name: 'main.ino.bin',
    mimeType: 'application/octet-stream',
    buffer: buildImage(0x70),
  });
  await page.locator('input[name="model"]').fill(model);
  await page.locator('input[name="version"]').fill('3.1.4');
  await page.locator('#signing-key').setInputFiles(privateKey);

  // Filled by the page, not by the test. A signature the server then accepts
  // is the only thing that proves the browser's RSA-PSS agrees with the one
  // the CLI and the device were built against.
  await expect(page.locator('textarea[name="signature"]')).not.toBeEmpty();

  await page.getByRole('button', { name: '上傳並發布' }).click();
  await expect(page.getByText(`韌體已發布：${model} 3.1.4。`)).toBeVisible();
  await expect(page.getByText(model).first()).toBeVisible();
});

test('the private key is never sent to the server', async ({ page }) => {
  // The key picker sits outside the form so that no attribute stands between
  // it and the wire, and this is what says so out loud.
  await register(page, uniqueEmail());

  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '在瀏覽器產生金鑰' }).click();
  const privateKey = await (await download).path();
  await page.getByRole('button', { name: '設定公鑰' }).click();
  await expect(page.getByText('已設定')).toBeVisible();

  const bodies: string[] = [];
  page.on('request', request => {
    const body = request.postData();
    if (body) bodies.push(body);
  });

  await page.locator('input[name="firmware"]').setInputFiles({
    name: 'main.ino.bin',
    mimeType: 'application/octet-stream',
    buffer: buildImage(0x71),
  });
  await page.locator('input[name="model"]').fill(`E2E-SECRET-${process.pid}`);
  await page.locator('input[name="version"]').fill('1.0.0');
  await page.locator('#signing-key').setInputFiles(privateKey);
  await expect(page.locator('textarea[name="signature"]')).not.toBeEmpty();
  await page.getByRole('button', { name: '上傳並發布' }).click();
  await expect(page.getByText('韌體已發布')).toBeVisible();

  expect(bodies.join('\n')).not.toContain('PRIVATE KEY');
});
