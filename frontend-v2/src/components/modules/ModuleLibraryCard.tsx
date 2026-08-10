import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import type { ModuleLibrary } from '@/types';
import { ExternalLink } from 'lucide-react';
import {
  getCapabilitySummary,
  getCompatibilitySummary,
  getSupportSemanticsSummary,
} from '@/lib/module-compatibility';

interface ModuleLibraryCardProps {
  module: ModuleLibrary;
  onAdd?: (module: ModuleLibrary) => void;
}

export function ModuleLibraryCard({ module, onAdd }: ModuleLibraryCardProps) {
  const compatibilitySummary = getCompatibilitySummary(module);
  const capabilitySummary = getCapabilitySummary(module);
  const supportSemanticsSummary = getSupportSemanticsSummary(module);

  return (
    <Card className="transition-all hover:shadow-md">
      <CardContent className="p-5 space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <h3 className="text-base font-semibold text-foreground truncate">{module.name}</h3>
            <p className="text-xs text-muted-foreground mt-0.5">v{module.version}</p>
          </div>
        </div>

        {module.description && (
          <p className="text-sm text-muted-foreground line-clamp-2">
            {module.description}
          </p>
        )}

        <div className="flex flex-wrap gap-2">
          <Badge variant="secondary" className="text-xs">
            {module.category}
          </Badge>
          <Badge variant="outline" className="text-xs">
            {module.provider}
          </Badge>
        </div>

        {module.variables && module.variables.length > 0 && (
          <div className="text-xs text-muted-foreground">
            {module.variables.length} variable{module.variables.length !== 1 ? 's' : ''}
          </div>
        )}

        {(compatibilitySummary || capabilitySummary || supportSemanticsSummary) && (
          <div className="space-y-1 text-xs text-muted-foreground">
            {compatibilitySummary && <p>{compatibilitySummary}</p>}
            {capabilitySummary && <p>{capabilitySummary}</p>}
            {supportSemanticsSummary && <p>{supportSemanticsSummary}</p>}
          </div>
        )}

        <div className="flex gap-2 pt-1">
          {onAdd && (
            <Button size="sm" onClick={() => onAdd(module)} className="flex-1">
              Add to Project
            </Button>
          )}
          {module.documentation_url && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => window.open(module.documentation_url, '_blank')}
            >
              <ExternalLink className="h-4 w-4" />
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
