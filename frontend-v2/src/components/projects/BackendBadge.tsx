import { Database, Cloud } from 'lucide-react';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';

interface BackendBadgeProps {
  backendType?: string;
  stateStorageProvider?: string;
  s3Bucket?: string;
  s3Region?: string;
  useNativeLocking?: boolean;
  s3Endpoint?: string;
  className?: string;
}

export function BackendBadge({
  backendType = 'local',
  stateStorageProvider,
  s3Bucket,
  s3Region,
  useNativeLocking = true,
  s3Endpoint,
  className = '',
}: BackendBadgeProps) {
  const isS3 = backendType === 's3';
  const isIbmCos = isS3 && stateStorageProvider === 'ibm';

  // Build tooltip content
  const getTooltipContent = () => {
    if (isS3) {
      return (
        <div className="space-y-1">
          <div className="font-semibold">{isIbmCos ? 'IBM COS State' : 'S3 State'}</div>
          {s3Bucket && (
            <div className="text-xs">
              <span className="text-muted-foreground">Bucket:</span> {s3Bucket}
            </div>
          )}
          {s3Region && (
            <div className="text-xs">
              <span className="text-muted-foreground">Region:</span> {s3Region}
            </div>
          )}
          {isIbmCos && s3Endpoint && (
            <div className="text-xs break-all">
              <span className="text-muted-foreground">Endpoint:</span> {s3Endpoint}
            </div>
          )}
          <div className="text-xs">
            <span className="text-muted-foreground">Locking:</span>{' '}
            {isIbmCos ? 'Managed automatically by BNK-Forge' : useNativeLocking ? 'Native S3 (no DynamoDB)' : 'DynamoDB'}
          </div>
          {isIbmCos && (
            <div className="text-xs text-muted-foreground">
              COS access is derived from the selected IBM credential template.
            </div>
          )}
        </div>
      );
    }

    return (
      <div className="space-y-1">
        <div className="font-semibold">Local Backend</div>
        <div className="text-xs text-muted-foreground">
          State stored in container volume
        </div>
      </div>
    );
  };

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <div
            tabIndex={0}
            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium whitespace-nowrap focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus:outline-none ${
              isS3
                ? 'bg-info/10 text-info border border-info/20'
                : 'bg-muted text-muted-foreground border border-border'
            } ${className}`}
          >
            {isS3 ? (
              <Cloud className="h-3 w-3" />
            ) : (
              <Database className="h-3 w-3" />
            )}
            <span>{isIbmCos ? 'COS' : isS3 ? 'S3' : 'Local'}</span>
          </div>
        </TooltipTrigger>
        <TooltipContent side="bottom" align="center">
          {getTooltipContent()}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

// Variant without tooltip for compact displays
export function BackendIcon({
  backendType = 'local',
  className = '',
}: {
  backendType?: string;
  className?: string;
}) {
  const isS3 = backendType === 's3';

  return (
    <div className={`inline-flex items-center ${className}`}>
      {isS3 ? (
        <Cloud className="h-3.5 w-3.5 text-info" />
      ) : (
        <Database className="h-3.5 w-3.5 text-muted-foreground" />
      )}
    </div>
  );
}
