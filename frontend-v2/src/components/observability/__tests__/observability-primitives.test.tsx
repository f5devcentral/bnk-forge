/**
 * Unit tests for the observability primitives' pure logic: formatters,
 * provider inference, and the TrendBadge / Gauge render paths.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { fmtInt, fmtCompact, fmtCost, fmtLatencyMs, fmtPct, inferProvider } from '../format';
import { providerColor, seriesColor } from '../chart-theme';
import { TrendBadge } from '../TrendBadge';
import { Gauge } from '../Gauge';

describe('formatters', () => {
  it('formats integers, compact, cost, latency, and pct', () => {
    expect(fmtInt(1234)).toBe('1,234');
    expect(fmtInt(undefined)).toBe('—');
    expect(fmtCompact(1500)).toBe('1.5K');
    expect(fmtCompact(2_400_000)).toBe('2.4M');
    expect(fmtCost(0)).toBe('$0.00');
    expect(fmtCost(0.0005)).toBe('$0.0005');
    expect(fmtCost(12.3)).toBe('$12.30');
    expect(fmtLatencyMs(842.3)).toBe('842.3 ms');
    expect(fmtPct(0.985)).toBe('98.5%');
  });
});

describe('inferProvider', () => {
  it('maps model families to providers', () => {
    expect(inferProvider('gpt-4o')).toBe('openai');
    expect(inferProvider('o3-mini')).toBe('openai');
    expect(inferProvider('claude-3-5-sonnet')).toBe('anthropic');
    expect(inferProvider('gemini-1.5-pro')).toBe('google');
    expect(inferProvider('llama-3')).toBe('unknown');
  });

  it('has stable provider + series colors', () => {
    expect(providerColor('openai')).toBe('#10b981');
    expect(providerColor('mystery')).toBe('#a3a3a3');
    expect(seriesColor(0)).toBe(seriesColor(9)); // palette cycles by length
  });
});

describe('TrendBadge', () => {
  it('renders "new" for a null delta', () => {
    render(<TrendBadge delta={null} />);
    expect(screen.getByText('new')).toBeInTheDocument();
  });

  it('renders a rounded percentage for a delta', () => {
    render(<TrendBadge delta={0.123} />);
    expect(screen.getByText('12%')).toBeInTheDocument();
  });
});

describe('Gauge', () => {
  it('clamps and renders the ratio as a percentage', () => {
    render(<Gauge value={0.42} label="hit rate" />);
    expect(screen.getByText('42%')).toBeInTheDocument();
    expect(screen.getByText('hit rate')).toBeInTheDocument();
  });
});
