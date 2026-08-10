/**
 * Chart Detail Panel
 *
 * Detail view for browsed Helm charts showing metadata and options
 */

import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Package,
  X,
  ExternalLink,
  Users,
  Code,
  Tag,
  Download,
  Globe,
} from 'lucide-react';

interface ChartDetailPanelProps {
  chart: {
    name: string;
    version: string;
    app_version?: string;
    description?: string;
    icon?: string;
    home?: string;
    sources?: string[];
    keywords?: string[];
    maintainers?: Array<{
      name: string;
      email?: string;
    }>;
  };
  onClose: () => void;
  onInstall?: () => void;
}

export function ChartDetailPanel({
  chart,
  onClose,
  onInstall,
}: ChartDetailPanelProps) {
  return (
    <div className="w-96 border-l border-border bg-card flex flex-col h-full">
      {/* Header */}
      <div className="px-4 py-3 border-b border-border">
        <div className="flex items-start justify-between mb-2">
          <div className="flex-1 min-w-0">
            {chart.icon ? (
              <img
                src={chart.icon}
                alt={chart.name}
                className="h-10 w-10 rounded mb-2"
                onError={(e) => {
                  e.currentTarget.style.display = 'none';
                }}
              />
            ) : (
              <Package className="h-10 w-10 mb-2 text-muted-foreground" />
            )}
            <h3 className="font-semibold text-lg truncate mb-1">
              {chart.name}
            </h3>
            <p className="text-xs text-muted-foreground">
              Helm Chart
            </p>
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="h-8 w-8 p-0 flex-shrink-0"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
            <span className="sr-only">Close</span>
          </Button>
        </div>

        <div className="flex items-center gap-2 mt-2">
          <Badge variant="outline" className="text-[10px]">
            <Tag className="h-3 w-3 mr-1" />
            v{chart.version}
          </Badge>
          {chart.app_version && (
            <Badge variant="outline" className="text-[10px]">
              App v{chart.app_version}
            </Badge>
          )}
        </div>
      </div>

      {/* Install Button */}
      <div className="px-4 py-2 border-b border-border">
        <Button
          className="w-full"
          onClick={onInstall}
        >
          <Download className="h-4 w-4 mr-2" />
          Install Chart
        </Button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4 space-y-6">
        {/* Description */}
        {chart.description && (
          <div>
            <h4 className="text-xs font-semibold mb-2 text-muted-foreground uppercase tracking-wide">Description</h4>
            <p className="text-sm text-foreground/80">{chart.description}</p>
          </div>
        )}

        {/* Keywords */}
        {chart.keywords && chart.keywords.length > 0 && (
          <div>
            <h4 className="text-xs font-semibold mb-2 text-muted-foreground uppercase tracking-wide">Keywords</h4>
            <div className="flex flex-wrap gap-2">
              {chart.keywords.map((keyword, index) => (
                <Badge
                  key={index}
                  variant="outline"
                  className="text-[10px]"
                >
                  {keyword}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {/* Maintainers */}
        {chart.maintainers && chart.maintainers.length > 0 && (
          <div>
            <h4 className="text-xs font-semibold mb-2 text-muted-foreground uppercase tracking-wide">Maintainers</h4>
            <div className="space-y-2">
              {chart.maintainers.map((maintainer, index) => (
                <div key={index} className="flex items-center gap-2">
                  <Users className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm truncate">{maintainer.name}</p>
                    {maintainer.email && (
                      <p className="text-xs truncate text-muted-foreground">
                        {maintainer.email}
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Links */}
        <div>
          <h4 className="text-xs font-semibold mb-2 text-muted-foreground uppercase tracking-wide">Links</h4>
          <div className="space-y-2">
            {chart.home && (
              <a
                href={chart.home}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 text-sm text-foreground/80 hover:text-primary transition-colors"
              >
                <Globe className="h-4 w-4" />
                <span>Homepage</span>
                <ExternalLink className="h-3 w-3 ml-auto" />
              </a>
            )}

            {chart.sources && chart.sources.length > 0 && (
              <>
                {chart.sources.map((source, index) => (
                  <a
                    key={index}
                    href={source}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-2 text-sm text-foreground/80 hover:text-primary transition-colors"
                  >
                    <Code className="h-4 w-4" />
                    <span className="truncate flex-1">Source Code</span>
                    <ExternalLink className="h-3 w-3" />
                  </a>
                ))}
              </>
            )}
          </div>
        </div>

        {/* Installation Info */}
        <div className="p-3 rounded-lg border border-border bg-muted/50">
          <h4 className="text-xs font-semibold mb-2 text-muted-foreground uppercase tracking-wide">Install Command</h4>
          <div className="p-2 rounded font-mono text-xs overflow-x-auto bg-card">
            <code>helm install my-release {chart.name}</code>
          </div>
        </div>

        {/* Note about README */}
        <div className="p-3 rounded-lg border border-info/20 bg-info/10 text-info text-xs">
          <p className="font-medium mb-1">Tip</p>
          <p>
            For detailed documentation, values schema, and README, visit the chart's homepage or source repository above.
          </p>
        </div>
      </div>
    </div>
  );
}
