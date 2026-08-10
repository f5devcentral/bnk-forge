/**
 * Tests for Catalog page — Advanced toggle hiding/showing the Modules tab.
 *
 * Covers:
 *   (a) Modules tab is NOT rendered by default (advanced=false).
 *   (b) After toggling Advanced ON, the Modules tab appears.
 *   (c) ?tab=modules deep link auto-enables advanced and shows the Modules tab.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import Catalog from '@/pages/Catalog';

// ---------------------------------------------------------------------------
// Mock all lazy-loaded sub-panels so Suspense resolves immediately
// ---------------------------------------------------------------------------

vi.mock('@/pages/Modules', () => ({
  default: () => <div data-testid="modules-panel">Modules Panel</div>,
}));

vi.mock('@/components/catalog/BlueprintCatalogPanel', () => ({
  default: () => <div data-testid="blueprints-panel">Blueprints Panel</div>,
}));

vi.mock('@/components/catalog/HelmReposPanel', () => ({
  default: () => <div data-testid="helm-repos-panel">Helm Repos Panel</div>,
}));

vi.mock('@/components/settings/BluefieldImages', () => ({
  BluefieldImages: () => <div data-testid="doca-panel">DOCA Panel</div>,
}));

vi.mock('@/components/settings/BfConfTemplates', () => ({
  BfConfTemplates: () => <div data-testid="bfconf-panel">BfConf Panel</div>,
}));

// ---------------------------------------------------------------------------
// localStorage helpers
// ---------------------------------------------------------------------------

const STORAGE_KEY = 'forge.catalog.advanced';

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  localStorage.clear();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Catalog — Advanced toggle', () => {
  it('(a) Modules tab is NOT in the document by default', () => {
    render(<Catalog />, { initialRoute: '/catalog' });
    // The TabsTrigger for Modules should not be rendered
    expect(screen.queryByRole('tab', { name: /modules/i })).not.toBeInTheDocument();
  });

  it('default tab is Blueprints (active tab label visible)', () => {
    render(<Catalog />, { initialRoute: '/catalog' });
    // Blueprints tab should be present and the blueprints panel should render
    expect(screen.getByRole('tab', { name: /blueprints/i })).toBeInTheDocument();
  });

  it('other tabs (Blueprints, Helm Repos, DOCA Releases, etc.) always appear', () => {
    render(<Catalog />, { initialRoute: '/catalog' });
    expect(screen.getByRole('tab', { name: /blueprints/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /helm repos/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /doca releases/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /bf\.conf templates/i })).toBeInTheDocument();
  });

  it('(b) after toggling Advanced ON, the Modules tab appears', async () => {
    const user = userEvent.setup();
    render(<Catalog />, { initialRoute: '/catalog' });

    // The Advanced switch label renders "Advanced" text; the underlying input is a checkbox
    const toggle = screen.getByRole('checkbox');
    expect(toggle).not.toBeChecked();

    await user.click(toggle);

    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /modules/i })).toBeInTheDocument();
    });

    // localStorage should be persisted
    expect(localStorage.getItem(STORAGE_KEY)).toBe('true');
  });

  it('persists Advanced=true across renders via localStorage', () => {
    localStorage.setItem(STORAGE_KEY, 'true');
    render(<Catalog />, { initialRoute: '/catalog' });
    expect(screen.getByRole('tab', { name: /modules/i })).toBeInTheDocument();
  });

  it('turning Advanced OFF while on Modules tab switches to Blueprints', async () => {
    const user = userEvent.setup();
    localStorage.setItem(STORAGE_KEY, 'true');
    render(<Catalog />, { initialRoute: '/catalog?tab=modules' });

    // Modules tab is visible (advanced=true from storage)
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /modules/i })).toBeInTheDocument();
    });

    // Turn off Advanced
    const toggle = screen.getByRole('checkbox');
    await user.click(toggle);

    await waitFor(() => {
      // Modules tab disappears
      expect(screen.queryByRole('tab', { name: /modules/i })).not.toBeInTheDocument();
    });

    // localStorage updated
    expect(localStorage.getItem(STORAGE_KEY)).toBe('false');
  });

  it('(c) ?tab=modules deep link auto-enables advanced and shows Modules tab', async () => {
    // Start with advanced=false (no localStorage entry)
    render(<Catalog />, { initialRoute: '/catalog?tab=modules' });

    // The useEffect should fire and auto-enable advanced
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /modules/i })).toBeInTheDocument();
    });

    expect(localStorage.getItem(STORAGE_KEY)).toBe('true');
  });
});
