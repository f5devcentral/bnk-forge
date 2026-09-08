/**
 * Tests for DPUDevice status derivation (`deviceStage`).
 *
 * Regression guard for the "Pending" bug: the DPUDevice CRD carries no
 * `status.phase`, so the row status is derived purely from lifecycle
 * conditions. Current DPF builds emit bare condition types (`Ready`,
 * `Discovered`, `Initialized`, `NodeAttached`); older builds prefixed them with
 * `DpuDevice`. `deviceStage` must handle both — otherwise a fully-provisioned
 * device shows as "Pending".
 */
import { describe, it, expect } from 'vitest';
import { deviceStage } from '../dpu-device-status';
import type { DpfK8sCondition } from '@/types';

const cond = (type: string, status: string): DpfK8sCondition => ({ type, status });

describe('deviceStage', () => {
  it('reports Ready for a fully-provisioned device using bare condition types', () => {
    // Exactly what the DPF provisioning API returns on healthy BF3 DPUs today.
    const conditions = [
      cond('Ready', 'True'),
      cond('Discovered', 'True'),
      cond('Initialized', 'True'),
      cond('NodeAttached', 'True'),
    ];
    expect(deviceStage(conditions)).toBe('Ready');
  });

  it('still reports Ready for legacy DpuDevice-prefixed condition types', () => {
    const conditions = [
      cond('DpuDeviceReady', 'True'),
      cond('DpuDeviceDiscovered', 'True'),
      cond('DpuDeviceInitialized', 'True'),
      cond('DpuDeviceNodeAttached', 'True'),
    ];
    expect(deviceStage(conditions)).toBe('Ready');
  });

  it('returns the highest stage reached during provisioning', () => {
    expect(
      deviceStage([cond('Discovered', 'True'), cond('NodeAttached', 'True')]),
    ).toBe('NodeAttached');
    expect(deviceStage([cond('Discovered', 'True')])).toBe('Discovered');
  });

  it('reports Error when it errored before reaching any stage (either spelling)', () => {
    // Error is a fallback: a positive lifecycle stage, if reached, takes
    // precedence — so Error surfaces only when no stage condition is True.
    expect(deviceStage([cond('Error', 'True')])).toBe('Error');
    expect(deviceStage([cond('DpuDeviceError', 'True')])).toBe('Error');
  });

  it('reports Pending when conditions exist but none are True', () => {
    expect(deviceStage([cond('Ready', 'False')])).toBe('Pending');
  });

  it('reports Unknown when there are no conditions', () => {
    expect(deviceStage(undefined)).toBe('Unknown');
    expect(deviceStage([])).toBe('Unknown');
  });
});
