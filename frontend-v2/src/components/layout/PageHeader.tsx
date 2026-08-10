/**
 * PageHeader — D-020 standard page chrome.
 *
 * Every page that is NOT a resource-explorer (i.e. not KubernetesV2 / F5BNK / CNF)
 * should use this as its top title block.  It replaces hand-rolled
 * `<h1 className="text-2xl font-bold …">` blocks.
 *
 * Props:
 *   title        — required page title
 *   subtitle     — optional secondary line (e.g. count / status summary)
 *   actions      — optional ReactNode rendered on the right (buttons, etc.)
 *   onRefresh    — optional; when set, renders the STANDARD refresh icon button
 *                  as the right-most action (same position + style on every
 *                  page, matching ResourcePageHeader's refresh).
 *   isRefreshing — spins the refresh icon while a refetch is in flight.
 *   className    — merged onto the outer div
 *
 * Layout: `flex items-start justify-between` — title+subtitle on left,
 * actions on right.  The caller wraps this in whatever spacing its page
 * container needs (typically it's the first child of a `space-y-6` div).
 *
 * The refresh button is always rendered LAST (far right) so its position is
 * identical regardless of what other actions a page supplies.
 */
import * as React from 'react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { RefreshCw } from 'lucide-react'

export interface PageHeaderProps {
  title: string
  subtitle?: React.ReactNode
  actions?: React.ReactNode
  onRefresh?: () => void
  isRefreshing?: boolean
  className?: string
}

export function PageHeader({
  title,
  subtitle,
  actions,
  onRefresh,
  isRefreshing,
  className,
}: PageHeaderProps) {
  const hasRight = actions || onRefresh
  return (
    <div className={cn('flex items-start justify-between gap-4', className)}>
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-foreground">{title}</h1>
        {subtitle && (
          <p className="text-sm text-muted-foreground mt-1">{subtitle}</p>
        )}
      </div>
      {hasRight && (
        <div className="flex items-center gap-2 flex-shrink-0">
          {actions}
          {onRefresh && (
            <Button
              variant="outline"
              size="sm"
              className="h-9 w-9 p-0"
              onClick={onRefresh}
              disabled={isRefreshing}
              title="Refresh"
              aria-label="Refresh"
            >
              <RefreshCw className={cn('h-4 w-4', isRefreshing && 'animate-spin')} aria-hidden="true" />
            </Button>
          )}
        </div>
      )}
    </div>
  )
}
