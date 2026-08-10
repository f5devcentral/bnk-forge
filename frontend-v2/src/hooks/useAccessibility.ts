import { useEffect, useState, useRef } from 'react';
import { useLocation } from 'react-router-dom';

// ---------------------------------------------------------------------------
// useReducedMotion — programmatic check for prefers-reduced-motion
// ---------------------------------------------------------------------------

/**
 * Returns `true` when the user has `prefers-reduced-motion: reduce` set.
 *
 * CSS utilities like `motion-safe:` handle most cases, but JS-driven
 * animations (e.g., scroll-into-view, requestAnimationFrame loops) need
 * a runtime boolean. This hook keeps it reactive — if the user toggles the
 * OS preference, the value updates immediately.
 *
 * @example
 * const reduced = useReducedMotion();
 * scrollTo({ behavior: reduced ? 'auto' : 'smooth' });
 */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => {
    if (typeof window === 'undefined') return false;
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  });

  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const handler = (e: MediaQueryListEvent) => setReduced(e.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);

  return reduced;
}

// ---------------------------------------------------------------------------
// useRouteAnnouncer — announce page title changes for screen readers
// ---------------------------------------------------------------------------

/**
 * Title map — maps route paths to human-readable page titles.
 * Expand as new routes are added.
 */
const ROUTE_TITLES: Record<string, string> = {
  '/': 'Dashboard',
  '/projects': 'Projects',
  '/modules': 'Modules',
  '/stacks': 'Blueprints',
  '/bnk': 'F5 BNK',
  '/kubernetes': 'Kubernetes',
  '/helm': 'Helm Packages',
  '/fleet': 'Fleet Overview',
  '/operators': 'Operators',
  '/tasks': 'Task History',
  '/system': 'System Settings',
  '/auth-templates': 'Auth Templates',
  '/workflows': 'Workflows',
};

/**
 * On every route change, announces the new page title via an `aria-live`
 * region so screen readers inform the user they've navigated.
 *
 * The live region is injected into the DOM once and persists for the
 * lifetime of the app.
 */
export function useRouteAnnouncer(): void {
  const location = useLocation();
  const regionRef = useRef<HTMLDivElement | null>(null);

  // Create the live region once
  useEffect(() => {
    if (regionRef.current) return;
    const div = document.createElement('div');
    div.setAttribute('role', 'status');
    div.setAttribute('aria-live', 'polite');
    div.setAttribute('aria-atomic', 'true');
    div.className = 'sr-only';
    div.id = 'route-announcer';
    document.body.appendChild(div);
    regionRef.current = div;
    return () => {
      div.remove();
      regionRef.current = null;
    };
  }, []);

  // Announce on route change
  useEffect(() => {
    if (!regionRef.current) return;

    const path = location.pathname;
    // Exact match first, then check dynamic routes
    let title = ROUTE_TITLES[path];
    if (!title) {
      if (path.startsWith('/projects/')) title = 'Project Detail';
      else title = 'Page';
    }

    // Small delay so the DOM update + aria announcement don't collide
    const timer = setTimeout(() => {
      if (regionRef.current) {
        regionRef.current.textContent = `Navigated to ${title}`;
      }
    }, 100);

    return () => clearTimeout(timer);
  }, [location.pathname]);
}

// ---------------------------------------------------------------------------
// useScrollToTop — scroll main content area to top on route change
// ---------------------------------------------------------------------------

/**
 * Scrolls the `#main-content` element to the top whenever the route changes.
 * Respects reduced-motion preference.
 */
export function useScrollToTop(): void {
  const location = useLocation();
  const reduced = useReducedMotion();

  useEffect(() => {
    const main = document.getElementById('main-content');
    if (main) {
      main.scrollTo({ top: 0, behavior: reduced ? 'auto' : 'smooth' });
    }
  }, [location.pathname, reduced]);
}
