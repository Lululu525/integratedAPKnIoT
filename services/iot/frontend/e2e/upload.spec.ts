import { Buffer } from 'node:buffer';
import { createHash, createSign, constants } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { buildImage } from './image';

// Seeded by e2e/backend.sh along with the public key its uploads are verified
// against. The account could equally have registered itself through the form;
// seeding just means the suite has one before the first page load.
const ADMIN = { email: 'admin@example.com', password: 'e2e-password' };

// The uploader's private key, written by generate_keys.py into the throwaway
// backend's KEYS_DIR. The server never sees it, which is the whole point of
// the change this signs for: a compromised server cannot produce firmware.
// `import.meta.url`, not `__dirname`: the suite is loaded as an ES module.
const PRIVATE_KEY = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  '.tmp',
  'keys',
  'private_key.pem',
);

// The same string the server rebuilds and checks: `model|version|sha256` over
// the whole file, RSA-PSS with MGF1-SHA256 and the maximum salt. Computed here
// rather than shelled out to sign_firmware.py so a mismatch between the two
// implementations of the manifest shows up as a failing test.
function signManifest(model: string, version: string, bytes: Buffer): string {
  const sha256 = createHash('sha256').update(bytes).digest('hex');
  const signer = createSign('sha256');
  signer.update(`${model}|${version}|${sha256}`);
  signer.end();
  return signer
    .sign({
      key: readFileSync(PRIVATE_KEY),
      padding: constants.RSA_PKCS1_PSS_PADDING,
      saltLength: constants.RSA_PSS_SALTLEN_MAX_SIGN,
    })
    .toString('base64');
}

// A model and a binary of its own per test. The cases share one database, so a
// fixed pair would make the duplicate case depend on the publish case having
// run first, and a retry would re-run a publish into the 409 it is not about.
let sequence = 0;

function uniqueBuild() {
  sequence += 1;
  return { model: `E2E-${process.pid}-${sequence}`, image: buildImage(0x10 + sequence) };
}

async function login(page: Page) {
  await page.goto('/login');
  // By autocomplete rather than by label: the labels are not bound to the
  // inputs, and what they read is still being settled. The field holds an
  // address now, but `username` is the autocomplete token for a sign-in
  // identity whatever that identity is.
  await page.locator('input[autocomplete="username"]').fill(ADMIN.email);
  await page.locator('input[autocomplete="current-password"]').fill(ADMIN.password);
  await page.getByRole('button', { name: '登入', exact: true }).click();
  await expect(page).toHaveURL('/');
}

async function publish(
  page: Page,
  { model, version, filename, bytes, signature }: {
    model: string;
    version: string;
    filename: string;
    bytes: Buffer;
    signature?: string;
  },
) {
  // By form field name, which is the half of the contract the backend reads.
  await page.locator('input[name="firmware"]').setInputFiles({
    name: filename,
    mimeType: 'application/octet-stream',
    buffer: bytes,
  });
  await page.locator('input[name="model"]').fill(model);
  await page.locator('input[name="version"]').fill(version);
  // Filled after the file, not before: picking one clears this field, because
  // a signature covers the hash of one exact image.
  await page
    .locator('textarea[name="signature"]')
    .fill(signature ?? signManifest(model, version, bytes));
  await page.getByRole('button', { name: '上傳並發布' }).click();
}

test('an account publishes a signed firmware and finds it in the list', async ({ page }) => {
  const { model, image } = uniqueBuild();

  await login(page);
  await publish(page, { model, version: '1.0.0', filename: 'main.ino.bin', bytes: image });

  // A dropped bearer header lands on the 401 branch instead, which is the
  // whole of what makes this assertion worth writing. The notice names what
  // the server published rather than what was typed, so an upload stored under
  // anything else fails here rather than on the list below.
  await expect(page.locator('.alert-info')).toHaveText(`韌體已發布：${model} 1.0.0。`);

  // No reload: a successful publish refetches the list, so what shows up here
  // came back from the server rather than from what the form believes it sent.
  const group = page.locator('.fw-group-card').filter({ hasText: model });
  await expect(group.getByText('最新 v1.0.0')).toBeVisible();
});

test('a file that is not an image reports why, not its status code', async ({ page }) => {
  const { model } = uniqueBuild();

  await login(page);
  // Named .bin so the input's accept filter is not what rejects it. The
  // signature is real and still irrelevant: the image is checked for structure
  // before anything is verified, so this fails on the file rather than on the
  // key, which is what the assertion below reads.
  const notAnImage = Buffer.alloc(2048, 0x78);
  await publish(page, {
    model,
    version: '1.0.0',
    filename: 'notes.bin',
    bytes: notAnImage,
  });

  // The positive assertion has to come first: it is what waits for the alert
  // to exist, and `not.toContainText` against a locator that never appears
  // passes on nothing.
  const alert = page.locator('.alert-error');
  await expect(alert).toContainText('Not an ESP32 image');
  await expect(alert).not.toContainText('HTTP 400');
});

test('re-uploading one binary under a second version names the first', async ({ page }) => {
  const { model, image } = uniqueBuild();

  await login(page);
  await publish(page, { model, version: '1.0.0', filename: 'main.ino.bin', bytes: image });
  await expect(page.locator('.alert-info')).toBeVisible();

  await publish(page, { model, version: '1.0.1', filename: 'main.ino.bin', bytes: image });

  const alert = page.locator('.alert-error');
  await expect(alert).toContainText('already uploaded as version 1.0.0');
  await expect(alert).not.toContainText('HTTP 409');
});

test('a signature from the wrong key is refused and nothing is published', async ({ page }) => {
  // The property per-tenant signing exists for, seen from the browser: the
  // server holds no key of its own, so there is nothing for it to fall back to
  // when the one on the account does not match.
  const { model, image } = uniqueBuild();

  await login(page);
  await publish(page, {
    model,
    version: '1.0.0',
    filename: 'main.ino.bin',
    bytes: image,
    signature: Buffer.from('not a signature').toString('base64'),
  });

  await expect(page.locator('.alert-error')).toContainText('signature');
  await expect(page.locator('.fw-group-card').filter({ hasText: model })).toHaveCount(0);
});

test('picking a second file clears the signature typed for the first', async ({ page }) => {
  // A signature covers one exact image. Left in place, it would travel with a
  // file it does not describe and fail on the server with a message about the
  // key rather than about the swap.
  const { model, image } = uniqueBuild();

  await login(page);
  await page.locator('input[name="firmware"]').setInputFiles({
    name: 'main.ino.bin',
    mimeType: 'application/octet-stream',
    buffer: image,
  });
  await page.locator('textarea[name="signature"]').fill(signManifest(model, '1.0.0', image));

  await page.locator('input[name="firmware"]').setInputFiles({
    name: 'other.bin',
    mimeType: 'application/octet-stream',
    buffer: uniqueBuild().image,
  });

  await expect(page.locator('textarea[name="signature"]')).toHaveValue('');
});
