/* Version comparison, mirroring the server and the device.

  "Latest for a model" has one right answer and three places that compute it:
  `parse_version` in backend/domain/signing.py, `parseVersionSegments` in
  esp32/main/ota.cpp, and this file. A dashboard that disagrees with the server
  reports the wrong devices as outdated. */

/* At most three dotted segments (1.2.3.4 truncates to 1.2.3), parsing stops at
  the first empty segment, and a segment's leading digits are all that count, so
  a non-numeric one reads as 0. The leniency mirrors `String::toInt()` on the
  device, which is what turns a garbage `FIRMWARE_VERSION` into 0 silently. */
export function parseVersion(version: string): number[] {
  const parts: string[] = [];
  let rest = version;
  while (parts.length < 2) {
    const dot = rest.indexOf('.');
    if (dot < 0) break;
    parts.push(rest.slice(0, dot));
    rest = rest.slice(dot + 1);
  }
  parts.push(rest);

  const segments: number[] = [];
  for (const part of parts) {
    if (part === '') break;
    const digits = /^[0-9]*/.exec(part)![0];
    segments.push(digits === '' ? 0 : Number(digits));
  }
  return segments;
}

/* Negative if a < b, positive if a > b, 0 if equal. Python compares the tuples
  element-wise and lets the shorter one lose a tie, so 1.2 < 1.2.0. */
export function compareVersions(a: string, b: string): number {
  const left = parseVersion(a);
  const right = parseVersion(b);
  for (let i = 0; i < Math.max(left.length, right.length); i++) {
    const l = left[i];
    const r = right[i];
    if (l === undefined) return -1;
    if (r === undefined) return 1;
    if (l !== r) return l - r;
  }
  return 0;
}

/* The version a device of this model would be offered on its next check: the
  highest active one, ties broken by id, exactly as `get_latest_for_model` does.
  Withdrawn rows are excluded, so a model whose newest version was withdrawn
  falls back to the one the server actually serves. */
export function pickLatestActive<T extends { version: string; active: boolean; id: number }>(
  items: T[],
): T | undefined {
  return items
    .filter(item => item.active)
    .reduce<T | undefined>((best, item) => {
      if (!best) return item;
      const order = compareVersions(item.version, best.version);
      return order > 0 || (order === 0 && item.id > best.id) ? item : best;
    }, undefined);
}
