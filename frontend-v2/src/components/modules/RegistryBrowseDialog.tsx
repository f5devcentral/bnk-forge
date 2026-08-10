import { useState } from 'react';
import { Search, Package, Download, CheckCircle, XCircle, Globe, Loader2, ExternalLink } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useRegistrySearch, useImportRegistryModule } from '@/hooks/useRegistry';
import { useDebounce } from '@/hooks/useDebounce';
import { DEBOUNCE_MS } from '@/lib/constants';
import { isAxiosError } from 'axios';
import { cn } from '@/lib/utils';
import { notify } from '@/lib/notify';
import type { RegistryModule } from '@/types';

interface RegistryBrowseDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onImported?: (module: { id: number; name: string }) => void;
}

export function RegistryBrowseDialog({ open, onOpenChange, onImported }: RegistryBrowseDialogProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedProvider, setSelectedProvider] = useState<string | undefined>();
  const [verifiedOnly, setVerifiedOnly] = useState<boolean | undefined>();
  const [selectedModule, setSelectedModule] = useState<RegistryModule | null>(null);

  const debouncedSearch = useDebounce(searchQuery, DEBOUNCE_MS.API_SEARCH);

  // Search registry modules
  const { data: searchResults, isLoading } = useRegistrySearch(
    debouncedSearch,
    selectedProvider,
    verifiedOnly,
    50,
    0,
    { enabled: open && debouncedSearch.length >= 3 }
  );

  const importMutation = useImportRegistryModule();

  const handleImport = async (module: RegistryModule) => {
    try {
      const result = await importMutation.mutateAsync({
        namespace: module.namespace,
        name: module.name,
        provider: module.provider,
        version: module.version,
        use_terraform_registry: searchResults?.source === 'terraform',
      });
      notify.success(`Module ${module.namespace}/${module.name} imported successfully`, undefined, { category: 'system' });
      setSelectedModule(null);
      if (onImported && result.module) {
        onImported({ id: result.module.id, name: result.module.name });
      }
    } catch (error: unknown) {
      const message = isAxiosError(error)
        ? error.response?.data?.detail || error.message
        : error instanceof Error ? error.message : 'Unknown error';
      notify.error(`Failed to import module: ${message}`);
    }
  };

  const resetFilters = () => {
    setSearchQuery('');
    setSelectedProvider(undefined);
    setVerifiedOnly(undefined);
    setSelectedModule(null);
  };

  const modules = searchResults?.modules || [];
  const registrySource = searchResults?.source;

  return (
    <Dialog
      open={open}
      onOpenChange={(newOpen) => {
        onOpenChange(newOpen);
        if (!newOpen) {
          resetFilters();
        }
      }}
    >
      <DialogContent className="max-w-6xl max-h-[90vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Globe className="h-5 w-5" />
            Browse OpenTofu Registry
          </DialogTitle>
          <DialogDescription>
            Search and import modules from OpenTofu Registry (with Terraform Registry fallback)
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 flex gap-4 overflow-hidden">
          {/* Left Panel - Search & Results */}
          <div className="flex-1 flex flex-col gap-4 overflow-hidden">
            {/* Search Bar & Filters */}
            <div className="space-y-3">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="Search modules (min 3 characters)..."
                  aria-label="Search module registry"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="pl-9"
                />
              </div>

              <div className="flex gap-2">
                <Select
                  value={selectedProvider}
                  onValueChange={(value) => setSelectedProvider(value === 'all' ? undefined : value)}
                >
                  <SelectTrigger className="w-[180px]">
                    <SelectValue placeholder="All Providers" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Providers</SelectItem>
                    <SelectItem value="aws">AWS</SelectItem>
                    <SelectItem value="azure">Azure</SelectItem>
                    <SelectItem value="google">Google Cloud</SelectItem>
                    <SelectItem value="ibm">IBM Cloud</SelectItem>
                    <SelectItem value="kubernetes">Kubernetes</SelectItem>
                    <SelectItem value="helm">Helm</SelectItem>
                  </SelectContent>
                </Select>

                <Select
                  value={verifiedOnly === true ? 'verified' : 'all'}
                  onValueChange={(value) => setVerifiedOnly(value === 'verified' ? true : undefined)}
                >
                  <SelectTrigger className="w-[180px]">
                    <SelectValue placeholder="All Modules" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Modules</SelectItem>
                    <SelectItem value="verified">Verified Only</SelectItem>
                  </SelectContent>
                </Select>

                {registrySource && (
                  <Badge variant="outline" className="ml-auto">
                    Source: {registrySource === 'opentofu' ? 'OpenTofu' : 'Terraform'} Registry
                  </Badge>
                )}
              </div>
            </div>

            {/* Results List */}
            <div className="flex-1 overflow-auto border rounded-md">
              {isLoading ? (
                <div className="flex items-center justify-center h-full">
                  <Loader2 className="h-6 w-6 animate-spin" />
                </div>
              ) : debouncedSearch.length < 3 ? (
                <div className="flex flex-col items-center justify-center h-full text-center p-8">
                  <Package className="h-12 w-12 text-muted-foreground mb-4" />
                  <p className="text-muted-foreground">
                    Enter at least 3 characters to search modules
                  </p>
                </div>
              ) : modules.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full text-center p-8">
                  <XCircle className="h-12 w-12 text-muted-foreground mb-4" />
                  <p className="text-muted-foreground">No modules found</p>
                  <p className="text-sm text-muted-foreground mt-2">
                    Try adjusting your search or filters
                  </p>
                </div>
              ) : (
                <div className="divide-y">
                  {modules.map((module) => (
                    <div
                      key={module.id}
                      className={cn(
                        'p-4 cursor-pointer transition-colors',
                        selectedModule?.id === module.id
                          ? 'bg-accent'
                          : 'hover:bg-accent/50',
                      )}
                      onClick={() => setSelectedModule(module)}
                    >
                      <div className="flex items-start justify-between gap-4">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-1">
                            <h4 className="font-semibold text-sm truncate">
                              {module.namespace}/{module.name}
                            </h4>
                            {module.verified && (
                              <Badge variant="default" className="text-xs">
                                <CheckCircle className="h-3 w-3 mr-1" />
                                Verified
                              </Badge>
                            )}
                          </div>
                          <div className="flex items-center gap-2 mb-2">
                            <Badge variant="outline" className="text-xs">
                              {module.provider}
                            </Badge>
                            <Badge variant="secondary" className="text-xs">
                              v{module.version}
                            </Badge>
                          </div>
                          <p className="text-xs text-muted-foreground line-clamp-2">
                            {module.description}
                          </p>
                          {module.downloads > 0 && (
                            <p className="text-xs text-muted-foreground mt-1">
                              {module.downloads.toLocaleString()} downloads
                            </p>
                          )}
                        </div>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleImport(module);
                          }}
                          disabled={importMutation.isPending}
                        >
                          <Download className="h-3 w-3 mr-1" />
                          Import
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Right Panel - Module Details */}
          {selectedModule && (
            <div className="w-96 border-l border-border pl-4 overflow-auto">
              <div className="space-y-4">
                <div>
                  <h3 className="font-semibold text-lg mb-2">
                    {selectedModule.namespace}/{selectedModule.name}
                  </h3>
                  <div className="flex flex-wrap gap-2 mb-3">
                    <Badge variant="outline">{selectedModule.provider}</Badge>
                    <Badge variant="secondary">v{selectedModule.version}</Badge>
                    {selectedModule.verified && (
                      <Badge variant="default">
                        <CheckCircle className="h-3 w-3 mr-1" />
                        Verified
                      </Badge>
                    )}
                  </div>
                  <p className="text-sm text-muted-foreground mb-4">
                    {selectedModule.description}
                  </p>
                </div>

                <div className="space-y-2">
                  <h4 className="font-semibold text-sm">Details</h4>
                  <div className="space-y-1 text-sm">
                    {selectedModule.downloads > 0 && (
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">Downloads:</span>
                        <span>{selectedModule.downloads.toLocaleString()}</span>
                      </div>
                    )}
                    {selectedModule.published_at && (
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">Published:</span>
                        <span>{new Date(selectedModule.published_at).toLocaleDateString()}</span>
                      </div>
                    )}
                    {selectedModule.source && (
                      <div className="flex justify-between items-center">
                        <span className="text-muted-foreground">Source:</span>
                        <a
                          href={selectedModule.source}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-primary hover:underline flex items-center gap-1"
                          onClick={(e) => e.stopPropagation()}
                        >
                          View
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      </div>
                    )}
                  </div>
                </div>

                <div className="pt-4">
                  <Button
                    className="w-full"
                    onClick={() => handleImport(selectedModule)}
                    disabled={importMutation.isPending}
                  >
                    {importMutation.isPending ? (
                      <>
                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                        Importing...
                      </>
                    ) : (
                      <>
                        <Download className="h-4 w-4 mr-2" />
                        Import to Library
                      </>
                    )}
                  </Button>
                </div>
              </div>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
