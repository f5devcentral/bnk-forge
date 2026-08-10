/**
 * Migration shared module (D-022 P5 S4 / D-022 P6 / D-023 P3).
 *
 * Exports the proxy/CIS migration cards + shared scan primitives used by:
 *   - ClusterScanResults (scan page — migration cards surfaced in P6 Slice A)
 *   - Fleet › Migration tab (D-022 P5 S4)
 *   - MigrationPanel (K8s page Migration tab — D-022 P6)
 *
 * CisMigrationCard and MigrationPanel depend on D-023 P3 (CIS discovery)
 * types (ClusterScanCis, useTranslateCis) which are now provided by D-023.
 */

export { ExistingProxiesCard } from './ExistingProxiesCard';
export { CisMigrationCard } from './CisMigrationCard';
export { MigrationPanel } from './MigrationPanel';
export { ScanCard, StatusBadge, StatusDot, statusConfig } from './ScanCard';
export { StatRow } from './StatRow';
export type { PrereqStatus } from './ScanCard';
