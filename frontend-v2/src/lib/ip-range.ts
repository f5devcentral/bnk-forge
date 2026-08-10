/**
 * IP address list / range parsing for the discovery wizard.
 *
 * Accepts tokens separated by newline, comma, or semicolon. Each token may be:
 *   - a single IPv4 address      ("198.18.0.19")
 *   - a last-octet range          ("198.18.0.19-22")
 *   - a full IPv4 range           ("198.18.0.19-198.18.0.22")
 */

const MAX_RANGE_SIZE = 256;

function parseOctet(value: string): number | null {
  if (!/^\d{1,3}$/.test(value)) return null;
  const n = Number.parseInt(value, 10);
  return n >= 0 && n <= 255 ? n : null;
}

function parseIp(value: string): number[] | null {
  const parts = value.split('.');
  if (parts.length !== 4) return null;
  const octets: number[] = [];
  for (const part of parts) {
    const octet = parseOctet(part);
    if (octet === null) return null;
    octets.push(octet);
  }
  return octets;
}

function ipToString(octets: number[]): string {
  return octets.join('.');
}

function ipToInt(octets: number[]): number {
  // Use multiplication to stay within safe integer range (no >>> 0 surprises).
  return octets[0] * 16777216 + octets[1] * 65536 + octets[2] * 256 + octets[3];
}

function intToIp(value: number): number[] {
  return [
    Math.floor(value / 16777216) & 0xff,
    Math.floor(value / 65536) & 0xff,
    Math.floor(value / 256) & 0xff,
    value & 0xff,
  ];
}

export interface ExpandResult {
  ips: string[];
  errors: string[];
}

export function expandIpToken(token: string): ExpandResult {
  const trimmed = token.trim();
  if (!trimmed) return { ips: [], errors: [] };

  if (!trimmed.includes('-')) {
    if (parseIp(trimmed) === null) {
      return { ips: [], errors: [`Invalid IP address: ${trimmed}`] };
    }
    return { ips: [trimmed], errors: [] };
  }

  const [startRaw, endRaw, ...extra] = trimmed.split('-');
  if (extra.length > 0 || !startRaw || !endRaw) {
    return { ips: [], errors: [`Invalid IP range: ${trimmed}`] };
  }

  const start = parseIp(startRaw.trim());
  if (!start) return { ips: [], errors: [`Invalid IP range start: ${trimmed}`] };

  let end: number[] | null;
  if (endRaw.includes('.')) {
    end = parseIp(endRaw.trim());
    if (!end) return { ips: [], errors: [`Invalid IP range end: ${trimmed}`] };
    // Reject ranges that span more than the last octet — would be confusing and risk huge expansions.
    if (start[0] !== end[0] || start[1] !== end[1] || start[2] !== end[2]) {
      return {
        ips: [],
        errors: [`IP range must share the first three octets: ${trimmed}`],
      };
    }
  } else {
    const lastOctet = parseOctet(endRaw.trim());
    if (lastOctet === null) {
      return { ips: [], errors: [`Invalid IP range end: ${trimmed}`] };
    }
    end = [start[0], start[1], start[2], lastOctet];
  }

  const startInt = ipToInt(start);
  const endInt = ipToInt(end);
  if (endInt < startInt) {
    return { ips: [], errors: [`IP range end is before start: ${trimmed}`] };
  }
  const count = endInt - startInt + 1;
  if (count > MAX_RANGE_SIZE) {
    return {
      ips: [],
      errors: [`IP range too large (${count} addresses, max ${MAX_RANGE_SIZE}): ${trimmed}`],
    };
  }

  const ips: string[] = [];
  for (let i = startInt; i <= endInt; i++) {
    ips.push(ipToString(intToIp(i)));
  }
  return { ips, errors: [] };
}

export interface ParsedIpInput {
  ips: string[];
  errors: string[];
}

export function parseIpInput(text: string): ParsedIpInput {
  const tokens = text
    .split(/[\n,;]+/)
    .map((t) => t.trim())
    .filter((t) => t.length > 0);

  const ips: string[] = [];
  const errors: string[] = [];
  const seen = new Set<string>();

  for (const token of tokens) {
    const result = expandIpToken(token);
    errors.push(...result.errors);
    for (const ip of result.ips) {
      if (!seen.has(ip)) {
        seen.add(ip);
        ips.push(ip);
      }
    }
  }

  return { ips, errors };
}
