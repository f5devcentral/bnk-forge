/**
 * DPUDevice status derivation.
 *
 * The DPUDevice CRD carries no `status.phase`, so a device's overall status is
 * derived purely from its lifecycle conditions. The DPF provisioning API
 * reports these as condition types: current builds use bare names (`Ready`,
 * `Discovered`, `Initialized`, `NodeAttached`); older builds prefixed them with
 * `DpuDevice` (`DpuDeviceReady`, …). {@link deviceStage} accepts either spelling
 * — without that, a fully-provisioned device whose conditions are all `True`
 * under the bare names matches nothing and its status falls through to
 * "Pending".
 */
import type { DpfK8sCondition } from '@/types';

/** Ordered DPUDevice lifecycle stages, lowest → highest. */
export const DEVICE_STAGES = [
  'Discovered',
  'NodeAttached',
  'Initialized',
  'Ready',
] as const;

/**
 * Find a lifecycle condition by stage name, tolerating the legacy `DpuDevice`
 * prefix so the same lookup works across DPF API versions.
 */
function findStageCondition(
  conditions: DpfK8sCondition[],
  stage: string,
): DpfK8sCondition | undefined {
  return conditions.find(
    (c) => c.type === stage || c.type === `DpuDevice${stage}`,
  );
}

/** Get the highest reached lifecycle stage for a DPUDevice. */
export function deviceStage(conditions: DpfK8sCondition[] | undefined): string {
  if (!conditions?.length) return 'Unknown';
  for (let i = DEVICE_STAGES.length - 1; i >= 0; i--) {
    if (findStageCondition(conditions, DEVICE_STAGES[i])?.status === 'True') {
      return DEVICE_STAGES[i];
    }
  }
  // Error is a fallback: a reached positive stage takes precedence, so this
  // surfaces only when no stage condition is True.
  if (findStageCondition(conditions, 'Error')?.status === 'True') return 'Error';
  return 'Pending';
}
