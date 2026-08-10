/**
 * ConnectivityGate — wraps a subtree that depends on a single remote target.
 *
 * Behavior:
 *   - state = REACHABLE          → render children
 *   - state = UNKNOWN (cold start) → render children (we don't know yet; let
 *     the children's own loading state show. UNKNOWN is honest, not "down")
 *   - state = UNREACHABLE        → render contextual offline banner instead
 *     of children. The children never mount, which kills their queries.
 *
 * Other targets are independent — wrapping a Pods panel for cluster A does
 * not affect a Pods panel on cluster B.
 *
 * Usage:
 *   <ConnectivityGate target={{ type: 'cluster', id: clusterId }}>
 *     <PodsPanel clusterId={clusterId} />
 *   </ConnectivityGate>
 */
import type { ReactNode } from 'react';
import { useTargetConnectivity } from '@/hooks/useConnectivity';
import { ConnectivityBanner } from './ConnectivityBanner';

interface ConnectivityGateProps {
  target: {
    type: string;
    id: number;
    displayName?: string;
  };
  children: ReactNode;
  /** When true, render children + a banner instead of replacing children. */
  staleData?: boolean;
}

export function ConnectivityGate({
  target,
  children,
  staleData = false,
}: ConnectivityGateProps) {
  const { state, isUnreachable, retry } = useTargetConnectivity(target.type, target.id);

  if (!isUnreachable) {
    // REACHABLE or UNKNOWN — let children render. UNKNOWN deliberately
    // doesn't gate: we don't yet have evidence the target is down, and the
    // child's own loading skeleton handles cold-start UX.
    return <>{children}</>;
  }

  if (staleData) {
    return (
      <>
        <ConnectivityBanner
          state={state}
          targetType={target.type}
          displayName={target.displayName}
          onRetry={retry}
        />
        {children}
      </>
    );
  }

  return (
    <ConnectivityBanner
      state={state}
      targetType={target.type}
      displayName={target.displayName}
      onRetry={retry}
    />
  );
}
