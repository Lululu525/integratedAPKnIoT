import { Buffer } from 'node:buffer';

// The layout backend/domain/firmware_image.py validates, little-endian. Enough
// of an ESP32 application image to be accepted, and nothing else: the contents
// carry no meaning and are never flashed.
const IMAGE_MAGIC = 0xe9;
const CHIP_ID_OFFSET = 12;
const HASH_APPENDED_OFFSET = 23;
const APP_DESC_OFFSET = 32;
const APP_DESC_MAGIC = 0xabcd5432;
const CHIP_ID_ESP32_S3 = 0x0009;
const MIN_FIRMWARE_BYTES = 1024;

export function buildImage(filler: number): Buffer {
  const image = Buffer.alloc(MIN_FIRMWARE_BYTES, filler);
  image[0] = IMAGE_MAGIC;
  image.writeUInt16LE(CHIP_ID_ESP32_S3, CHIP_ID_OFFSET);
  image.writeUInt32LE(APP_DESC_MAGIC, APP_DESC_OFFSET);
  // Claim no trailing digest, a legitimate build option. Leaving the flag to
  // the filler would demand a real SHA-256 over the padding whenever it landed
  // on 1.
  image[HASH_APPENDED_OFFSET] = 0;
  return image;
}
