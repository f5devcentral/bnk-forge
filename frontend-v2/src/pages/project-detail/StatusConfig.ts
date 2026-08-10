/**
 * Status configuration constants for module and stack badges
 *
 * D-020: token-pure. The `color` field on statusConfig/defaultStatus is a
 * solid background class kept for legacy callers (currently none consume it —
 * StateInfoPopover uses getModuleStatusColor() instead). stackStatusConfig
 * emits a triplet (text/bg/border) consumed via className on a Badge.
 */

import React from 'react';
import { CheckCircle2, XCircle, Loader2, Box, Clock } from 'lucide-react';

export const statusConfig: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
  applied: { color: 'bg-success', icon: React.createElement(CheckCircle2, { className: 'h-3.5 w-3.5' }), label: 'Deployed' },
  initializing: { color: 'bg-warning', icon: React.createElement(Loader2, { className: 'h-3.5 w-3.5 animate-spin' }), label: 'Initializing' },
  initialized: { color: 'bg-info', icon: React.createElement(Box, { className: 'h-3.5 w-3.5' }), label: 'Ready' },
  init_failed: { color: 'bg-destructive', icon: React.createElement(XCircle, { className: 'h-3.5 w-3.5' }), label: 'Init Failed' },
  planning: { color: 'bg-warning', icon: React.createElement(Loader2, { className: 'h-3.5 w-3.5 animate-spin' }), label: 'Planning' },
  planned: { color: 'bg-info', icon: React.createElement(CheckCircle2, { className: 'h-3.5 w-3.5' }), label: 'Planned' },
  plan_failed: { color: 'bg-destructive', icon: React.createElement(XCircle, { className: 'h-3.5 w-3.5' }), label: 'Plan Failed' },
  applying: { color: 'bg-warning', icon: React.createElement(Loader2, { className: 'h-3.5 w-3.5 animate-spin' }), label: 'Applying' },
  apply_failed: { color: 'bg-destructive', icon: React.createElement(XCircle, { className: 'h-3.5 w-3.5' }), label: 'Apply Failed' },
  destroying: { color: 'bg-warning', icon: React.createElement(Loader2, { className: 'h-3.5 w-3.5 animate-spin' }), label: 'Destroying' },
  destroyed: { color: 'bg-muted', icon: React.createElement(XCircle, { className: 'h-3.5 w-3.5' }), label: 'Destroyed' },
  destroy_failed: { color: 'bg-destructive', icon: React.createElement(XCircle, { className: 'h-3.5 w-3.5' }), label: 'Destroy Failed' },
  pending: { color: 'bg-muted', icon: React.createElement(Box, { className: 'h-3.5 w-3.5' }), label: 'Pending' },
  not_initialized: { color: 'bg-muted', icon: React.createElement(Box, { className: 'h-3.5 w-3.5' }), label: 'Pending' },
  failed: { color: 'bg-destructive', icon: React.createElement(XCircle, { className: 'h-3.5 w-3.5' }), label: 'Failed' },
};

export const defaultStatus = { color: 'bg-muted', icon: React.createElement(Box, { className: 'h-3.5 w-3.5' }), label: 'Unknown' };

// Stack status config for badge display in collapsible headers
export const stackStatusConfig: Record<string, { icon: typeof Clock; color: string; label: string }> = {
  pending: { icon: Clock, color: 'text-muted-foreground bg-muted/50 border-border', label: 'Pending' },
  deploying: { icon: Loader2, color: 'text-info bg-info/10 border-info/20', label: 'Deploying' },
  deployed: { icon: CheckCircle2, color: 'text-success bg-success/10 border-success/20', label: 'Deployed' },
  failed: { icon: XCircle, color: 'text-destructive bg-destructive/10 border-destructive/20', label: 'Failed' },
  destroying: { icon: Loader2, color: 'text-warning bg-warning/10 border-warning/20', label: 'Destroying' },
  destroyed: { icon: CheckCircle2, color: 'text-muted-foreground bg-muted/50 border-border', label: 'Destroyed' },
};
