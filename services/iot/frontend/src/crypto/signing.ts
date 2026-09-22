// Key generation and manifest signing, done in the browser so that publishing
// needs nothing but a browser. The private key is generated here, handed to
// the operator as a file, and read back here at publish time; it is never sent
// anywhere. That is the same arrangement as the CLI scripts, with the operator's
// machine being the browser rather than a checkout of this repo.
//
// The manifest and the padding have to match what backend/domain/signing.py
// verifies and what ota.cpp verifies again on the device. See "The signing
// contract" in CLAUDE.local.md before changing anything here.

// WebCrypto is only exposed in a secure context. Loopback counts as one, so
// dev works, but a frontend served over plain HTTP from a LAN address does
// not, and the failure is `crypto.subtle` being undefined rather than an
// error anything would catch.
class NoWebCrypto extends Error {
  constructor() {
    super('這個瀏覽器分頁沒有 WebCrypto，只有 HTTPS 或 localhost 才有。');
  }
}

function subtle(): SubtleCrypto {
  if (!globalThis.crypto?.subtle) throw new NoWebCrypto();
  return globalThis.crypto.subtle;
}

const RSA_PSS_SHA256 = { name: 'RSA-PSS', hash: 'SHA-256' } as const;
const MODULUS_BITS = 2048;
const DIGEST_BYTES = 32;

function toBase64(bytes: ArrayBuffer): string {
  let binary = '';
  for (const byte of new Uint8Array(bytes)) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function fromBase64(value: string): Uint8Array {
  const binary = atob(value);
  return Uint8Array.from(binary, c => c.charCodeAt(0));
}

// 64 characters a line, which is what every PEM writer emits and what
// `cryptography` produces when it re-serializes the key on the way in.
function toPem(label: string, der: ArrayBuffer): string {
  const body = toBase64(der).replace(/(.{64})/g, '$1\n').trimEnd();
  return `-----BEGIN ${label}-----\n${body}\n-----END ${label}-----\n`;
}

function fromPem(label: string, pem: string): Uint8Array {
  // Tolerant of what a file round-tripped through an editor looks like: CRLF,
  // a missing trailing newline, whitespace either side.
  const match = pem.match(
    new RegExp(`-----BEGIN ${label}-----([\\s\\S]*?)-----END ${label}-----`)
  );
  if (!match) throw new Error(`這不是一個 ${label} 檔案。`);
  return fromBase64(match[1].replace(/\s+/g, ''));
}

/** The largest salt this key can carry, which is what the CLI signers use.
 *
 * Verifiers do not pin a length (the server uses PSS.AUTO, the device's
 * mbedtls recovers it), so any value would be accepted. Matching the other
 * signers keeps one answer to "what does a signature from this project look
 * like" rather than three.
 */
function maxSaltLength(modulusBits: number): number {
  const emLen = Math.ceil((modulusBits - 1) / 8);
  return emLen - DIGEST_BYTES - 2;
}

export type GeneratedKeys = { publicPem: string; privatePem: string };

export async function generateKeyPair(): Promise<GeneratedKeys> {
  const pair = await subtle().generateKey(
    {
      ...RSA_PSS_SHA256,
      modulusLength: MODULUS_BITS,
      publicExponent: new Uint8Array([1, 0, 1]),
    },
    // Extractable, because the whole point is to hand the private half to the
    // operator as a file. The key imported at publish time is not.
    true,
    ['sign', 'verify']
  );
  const [spki, pkcs8] = await Promise.all([
    subtle().exportKey('spki', pair.publicKey),
    subtle().exportKey('pkcs8', pair.privateKey),
  ]);
  return {
    publicPem: toPem('PUBLIC KEY', spki),
    privatePem: toPem('PRIVATE KEY', pkcs8),
  };
}

export type SigningKey = { key: CryptoKey; saltLength: number };

/** Read a PKCS#8 PEM into a key that can sign and can no longer be exported. */
export async function importPrivateKey(pem: string): Promise<SigningKey> {
  const der = fromPem('PRIVATE KEY', pem);
  // TypeScript 6 models Uint8Array buffers as ArrayBufferLike, while
  // WebCrypto's importKey overload accepts an owned ArrayBuffer. Copy the
  // decoded bytes into an explicit ArrayBuffer so the production build stays
  // type-safe on current DOM declarations.
  const keyData = new ArrayBuffer(der.byteLength);
  new Uint8Array(keyData).set(der);
  let key: CryptoKey;
  try {
    key = await subtle().importKey('pkcs8', keyData, RSA_PSS_SHA256, false, ['sign']);
  } catch {
    // WebCrypto rejects everything with the same DataError, and the likely
    // causes are worth naming: an RSA key in the older PKCS#1 form, or the
    // public half picked by mistake.
    throw new Error('讀不到這個私鑰。需要 PKCS#8 格式（開頭是 BEGIN PRIVATE KEY）。');
  }
  const modulusBits = (key.algorithm as RsaHashedKeyAlgorithm).modulusLength;
  return { key, saltLength: maxSaltLength(modulusBits) };
}

export async function sha256Hex(bytes: ArrayBuffer): Promise<string> {
  const digest = await subtle().digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, '0')).join('');
}

/** Sign `model|version|sha256_hex`, the one string all three parties rebuild. */
export async function signManifest(
  signing: SigningKey,
  model: string,
  version: string,
  sha256hex: string
): Promise<string> {
  const manifest = new TextEncoder().encode(`${model}|${version}|${sha256hex}`);
  const signature = await subtle().sign(
    { name: 'RSA-PSS', saltLength: signing.saltLength },
    signing.key,
    manifest
  );
  return toBase64(signature);
}
