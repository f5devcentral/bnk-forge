import { Badge } from '@/components/ui/badge';
import { AlertCircle, Clock, CheckCircle2 } from 'lucide-react';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';

interface ExpirationBadgeProps {
  expiryDate?: string;
  label?: string;
}

export function ExpirationBadge({ expiryDate, label = 'Credentials' }: ExpirationBadgeProps) {
  if (!expiryDate) {
    return null;
  }

  // Backend sends UTC timestamps without 'Z' suffix, so we need to append it
  // to ensure JavaScript interprets them as UTC, not local time
  const expiryString = expiryDate.endsWith('Z') ? expiryDate : `${expiryDate}Z`;
  const expiry = new Date(expiryString);
  const now = new Date();
  const timeUntilExpiry = expiry.getTime() - now.getTime();
  const hoursUntilExpiry = timeUntilExpiry / (1000 * 60 * 60);
  const minutesUntilExpiry = timeUntilExpiry / (1000 * 60);

  // Expired
  if (timeUntilExpiry <= 0) {
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge variant="destructive" className="gap-1">
              <AlertCircle className="h-3 w-3" />
              Expired
            </Badge>
          </TooltipTrigger>
          <TooltipContent>
            <p className="font-medium">{label} expired</p>
            <p className="text-xs text-muted-foreground">
              {expiry.toLocaleString()}
            </p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  // Expiring within 1 hour
  if (hoursUntilExpiry < 1) {
    const mins = Math.floor(minutesUntilExpiry);
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge variant="destructive" className="gap-1">
              <AlertCircle className="h-3 w-3" />
              Expiring Soon
            </Badge>
          </TooltipTrigger>
          <TooltipContent>
            <p className="font-medium">{label} expire in {mins} minute{mins !== 1 ? 's' : ''}</p>
            <p className="text-xs text-muted-foreground">
              {expiry.toLocaleString()}
            </p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  // Expiring within 24 hours
  if (hoursUntilExpiry < 24) {
    const hours = Math.floor(hoursUntilExpiry);
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge variant="warning" className="gap-1">
              <Clock className="h-3 w-3" />
              {hours}h left
            </Badge>
          </TooltipTrigger>
          <TooltipContent>
            <p className="font-medium">{label} expire in {hours} hour{hours !== 1 ? 's' : ''}</p>
            <p className="text-xs text-muted-foreground">
              {expiry.toLocaleString()}
            </p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  // More than 24 hours - show days
  const daysUntilExpiry = Math.floor(hoursUntilExpiry / 24);
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge variant="success" className="gap-1">
            <CheckCircle2 className="h-3 w-3" />
            {daysUntilExpiry}d left
          </Badge>
        </TooltipTrigger>
        <TooltipContent>
          <p className="font-medium">{label} expire in {daysUntilExpiry} day{daysUntilExpiry !== 1 ? 's' : ''}</p>
          <p className="text-xs text-muted-foreground">
            {expiry.toLocaleString()}
          </p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
