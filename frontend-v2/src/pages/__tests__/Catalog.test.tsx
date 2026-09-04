/**
 * Tests for Catalog page — Streamlined 4-tab architecture.
 */
import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import Catalog from '@/pages/Catalog';

// Mock all lazy-loaded sub-panels so Suspense resolves immediately
vi.mock('@/pages/Modules', () => ({
  default: () => <div data-testid="modules-panel">Modules Panel</div>,
}));

vi.mock('@/components/catalog/BlueprintCatalogPanel', () => ({
  default: () => <div data-testid="blueprints-panel">Blueprints Panel</div>,
}));

vi.mock('@/components/catalog/HelmReposPanel', () => ({
  default: () => <div data-testid="helm-repos-panel">Helm Repos Panel</div>,
}));

vi.mock('@/components/catalog/SystemImagesPanel', () => ({
  SystemImagesPanel: () => <div data-testid="system-images-panel">System Images Panel</div>,
  default: () => <div data-testid="system-images-panel">System Images Panel</div>,
}));

describe('Catalog — 4-tab navigation', () => {
  it('renders all 4 primary tabs by default without requiring an advanced switch', () => {
    render(<Catalog />, { initialRoute: '/catalog' });
    expect(screen.getByRole('tab', { name: /blueprints & stacks/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /modules/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /helm charts & repos/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /system & dpu images/i })).toBeInTheDocument();
  });

  it('default tab is Blueprints & Stacks', async () => {
    render(<Catalog />, { initialRoute: '/catalog' });
    await waitFor(() => {
      expect(screen.getByTestId('blueprints-panel')).toBeInTheDocument();
    });
  });

  it('switches between tabs on click', async () => {
    const user = userEvent.setup();
    render(<Catalog />, { initialRoute: '/catalog' });

    const modulesTab = screen.getByRole('tab', { name: /modules/i });
    await user.click(modulesTab);

    await waitFor(() => {
      expect(screen.getByTestId('modules-panel')).toBeInTheDocument();
    });
  });

  it('supports direct deep linking to ?tab=modules', async () => {
    render(<Catalog />, { initialRoute: '/catalog?tab=modules' });
    await waitFor(() => {
      expect(screen.getByTestId('modules-panel')).toBeInTheDocument();
    });
  });

  it('supports direct deep linking to legacy system image tab names like ?tab=doca-releases', async () => {
    render(<Catalog />, { initialRoute: '/catalog?tab=doca-releases' });
    await waitFor(() => {
      expect(screen.getByTestId('system-images-panel')).toBeInTheDocument();
    });
  });
});
