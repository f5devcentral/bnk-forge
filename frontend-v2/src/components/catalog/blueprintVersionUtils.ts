/**
 * Semver-tolerant comparator for blueprint version strings.
 *
 * Ordering rules:
 * - Stable semver (e.g. "1.2.3") is always greater than the matching pre-release
 *   (e.g. "1.2.3-ehf-..." or "1.2.3-rc.1").
 * - Two stable semver strings compare numerically, major → minor → patch.
 * - Two pre-release strings compare their base semver first, then lexicographically
 *   by the full string as a tiebreak.
 * - Strings that do not parse as semver at all fall back to lexicographic comparison.
 *
 * Returns a negative number when a < b (i.e. b is newer), positive when a > b.
 * Suitable for `Array.prototype.sort` — pass compareBlueprintVersions directly.
 * To get newest-first: `versions.sort(compareBlueprintVersions).reverse()`
 * or use the exported `sortVersionsDesc` helper.
 */

const SEMVER_RE = /^(\d+)\.(\d+)\.(\d+)(?:-(.+))?$/;

interface ParsedVersion {
  major: number;
  minor: number;
  patch: number;
  pre: string | null; // null means stable
  raw: string;
}

function parseVersion(v: string): ParsedVersion | null {
  const m = SEMVER_RE.exec(v);
  if (!m) return null;
  return {
    major: parseInt(m[1], 10),
    minor: parseInt(m[2], 10),
    patch: parseInt(m[3], 10),
    pre: m[4] ?? null,
    raw: v,
  };
}

/**
 * Compare two blueprint version strings.
 * Returns positive if a > b (a is newer), negative if a < b, 0 if equal.
 */
export function compareBlueprintVersions(a: string, b: string): number {
  const pa = parseVersion(a);
  const pb = parseVersion(b);

  // If neither parses, fall back to lexicographic
  if (!pa && !pb) return a.localeCompare(b);
  // Non-parseable version is treated as older
  if (!pa) return -1;
  if (!pb) return 1;

  // Compare major.minor.patch numerically
  if (pa.major !== pb.major) return pa.major - pb.major;
  if (pa.minor !== pb.minor) return pa.minor - pb.minor;
  if (pa.patch !== pb.patch) return pa.patch - pb.patch;

  // Same base version: stable > pre-release
  if (pa.pre === null && pb.pre !== null) return 1;
  if (pa.pre !== null && pb.pre === null) return -1;
  if (pa.pre !== null && pb.pre !== null) return pa.pre.localeCompare(pb.pre);

  return 0;
}

/** Returns a new array sorted newest-version-first. */
export function sortVersionsDesc<T>(items: T[], getVersion: (item: T) => string): T[] {
  return [...items].sort((a, b) => compareBlueprintVersions(getVersion(b), getVersion(a)));
}
