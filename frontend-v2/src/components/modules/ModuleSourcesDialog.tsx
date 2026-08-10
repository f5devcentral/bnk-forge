import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { moduleSourceSchema, type ModuleSourceFormData } from '@/schemas';
import { Plus, Trash2, RefreshCw, Edit2, GitBranch, Globe, CheckCircle2, XCircle, Loader2, Clock } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Checkbox } from '@/components/ui/checkbox';
import {
  useModuleSources,
  useCreateModuleSource,
  useUpdateModuleSource,
  useDeleteModuleSource,
  useSyncModuleSource,
} from '@/hooks/useModuleSources';
import type { ModuleSource, ModuleSourceCreate, ModuleSourceUpdate } from '@/types';
import { formatAge } from '@/lib/time-utils';

// Popular Terraform/OpenTofu module repositories for quick add
// Note: branch field specifies the correct default branch for each repo
const POPULAR_MODULE_SOURCES = [
  { name: 'terraform-aws-vpc', url: 'https://github.com/terraform-aws-modules/terraform-aws-vpc.git', branch: 'master', description: 'Official AWS VPC module' },
  { name: 'terraform-aws-eks', url: 'https://github.com/terraform-aws-modules/terraform-aws-eks.git', branch: 'master', description: 'Official AWS EKS module' },
  { name: 'cloudposse-components', url: 'https://github.com/cloudposse/terraform-aws-components.git', branch: 'main', description: 'Cloud Posse AWS Components' },
  { name: 'terraform-google-network', url: 'https://github.com/terraform-google-modules/terraform-google-network.git', branch: 'master', description: 'Official GCP Network module' },
  { name: 'terraform-azurerm-network', url: 'https://github.com/Azure/terraform-azurerm-network.git', branch: 'main', description: 'Official Azure Network module' },
  { name: 'hashicorp-consul', url: 'https://github.com/hashicorp/terraform-aws-consul.git', branch: 'master', description: 'HashiCorp Consul on AWS' },
];

interface ModuleSourcesDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ModuleSourcesDialog({ open, onOpenChange }: ModuleSourcesDialogProps) {
  const [isAddDialogOpen, setIsAddDialogOpen] = useState(false);
  const [editingSource, setEditingSource] = useState<ModuleSource | null>(null);

  const { data: sources, isLoading } = useModuleSources();
  const createMutation = useCreateModuleSource();
  const updateMutation = useUpdateModuleSource();
  const deleteMutation = useDeleteModuleSource();
  const syncMutation = useSyncModuleSource();

  const handleAdd = () => {
    setEditingSource(null);
    setIsAddDialogOpen(true);
  };

  const handleEdit = (source: ModuleSource) => {
    setEditingSource(source);
    setIsAddDialogOpen(true);
  };

  const handleDelete = (source: ModuleSource) => {
    if (source.source_type === 'builtin') {
      return; // Cannot delete built-in source
    }

    if (confirm(`Delete module source "${source.name}"?\n\nThis will not delete modules that were imported from this source.`)) {
      deleteMutation.mutate(source.id);
    }
  };

  const handleSync = (source: ModuleSource) => {
    syncMutation.mutate(source.id);
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'success':
        return <CheckCircle2 className="h-4 w-4 text-success" />;
      case 'failed':
        return <XCircle className="h-4 w-4 text-destructive" />;
      case 'syncing':
        return <Loader2 className="h-4 w-4 text-info animate-spin" />;
      default:
        return <Clock className="h-4 w-4 text-muted-foreground" />;
    }
  };

  const getStatusBadge = (status: string) => {
    const variants: Record<string, "success" | "info" | "destructive" | "outline"> = {
      success: 'success',
      failed: 'destructive',
      syncing: 'info',
      pending: 'outline',
    };
    return <Badge variant={variants[status] || 'outline'}>{status}</Badge>;
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-5xl max-h-[85vh] overflow-hidden flex flex-col">
          <DialogHeader>
            <DialogTitle>Module Sources</DialogTitle>
            <DialogDescription>
              Manage external module sources from Git repositories and OpenTofu Registry
            </DialogDescription>
          </DialogHeader>

          <div className="flex-1 overflow-auto">
            {isLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-6 w-6 animate-spin" />
              </div>
            ) : !sources || sources.length === 0 ? (
              <Alert>
                <AlertDescription>
                  No module sources configured. Click "Add Source" to get started.
                </AlertDescription>
              </Alert>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>URL</TableHead>
                    <TableHead>Modules</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Last Synced</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sources.map((source) => (
                    <TableRow key={source.id}>
                      <TableCell className="font-medium">
                        <div className="flex items-center gap-2">
                          <span>{source.name}</span>
                          {source.is_default ? <Badge variant="secondary">Default</Badge> : null}
                        </div>
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          {source.source_type === 'git' ? (
                            <GitBranch className="h-4 w-4" />
                          ) : (
                            <Globe className="h-4 w-4" />
                          )}
                          <span className="capitalize">{source.source_type}</span>
                        </div>
                      </TableCell>
                      <TableCell className="max-w-xs truncate text-sm text-muted-foreground">
                        {source.url}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">{source.module_count}</Badge>
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          {getStatusIcon(source.sync_status)}
                          {getStatusBadge(source.sync_status)}
                        </div>
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {source.last_synced_at
                          ? formatAge(source.last_synced_at)
                          : 'Never'}
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex items-center justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleSync(source)}
                            disabled={syncMutation.isPending || source.sync_status === 'syncing'}
                          >
                            <RefreshCw className={`h-4 w-4 ${syncMutation.isPending ? 'animate-spin' : ''}`} />
                          </Button>
                          {source.source_type !== 'builtin' && (
                            <>
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => handleEdit(source)}
                              >
                                <Edit2 className="h-4 w-4" />
                              </Button>
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => handleDelete(source)}
                                disabled={deleteMutation.isPending}
                              >
                                <Trash2 className="h-4 w-4" />
                              </Button>
                            </>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Close
            </Button>
            <Button onClick={handleAdd}>
              <Plus className="h-4 w-4 mr-2" />
              Add Source
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {isAddDialogOpen && (
        <AddEditSourceDialog
          open={isAddDialogOpen}
          onOpenChange={setIsAddDialogOpen}
          source={editingSource}
          isSubmitting={createMutation.isPending || updateMutation.isPending}
          onSubmit={(data) => {
            if (editingSource) {
              updateMutation.mutate(
                { sourceId: editingSource.id, data },
                {
                  onSuccess: () => {
                    setIsAddDialogOpen(false);
                    setEditingSource(null);
                  },
                }
              );
            } else {
              createMutation.mutate(data as ModuleSourceCreate, {
                onSuccess: () => {
                  setIsAddDialogOpen(false);
                },
              });
            }
          }}
        />
      )}
    </>
  );
}

interface AddEditSourceDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  source?: ModuleSource | null;
  isSubmitting?: boolean;
  onSubmit: (data: ModuleSourceCreate | ModuleSourceUpdate) => void;
}

function AddEditSourceDialog({ open, onOpenChange, source, isSubmitting, onSubmit }: AddEditSourceDialogProps) {
  const form = useForm<ModuleSourceFormData>({
    resolver: zodResolver(moduleSourceSchema),
    defaultValues: {
      name: source?.name || '',
      source_type: source?.source_type || 'git',
      url: source?.url || '',
      branch: source?.branch || 'main',
      git_ref: source?.git_ref || '',
      auth_type: source?.auth_type || 'none',
      auth_token: '',
      auto_sync: source?.auto_sync || false,
      sync_interval_hours: source?.sync_interval_hours || 24,
      description: source?.description || '',
      make_default: source?.is_default || false,
    },
  });

  const sourceType = form.watch('source_type');
  const authType = form.watch('auth_type');
  const syncInterval = form.watch('sync_interval_hours');

  const onValid = (values: ModuleSourceFormData) => {
    onSubmit(values);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onValid)} noValidate>
            <DialogHeader>
              <DialogTitle>{source ? 'Edit Module Source' : 'Add Module Source'}</DialogTitle>
              <DialogDescription>
                Configure a Git repository or Registry as a module source
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-4 py-4">
              {/* Popular Sources Quick Add - only show when adding new */}
              {!source && (
                <div className="space-y-2">
                  <Label className="text-xs text-muted-foreground">Quick Add Popular Source</Label>
                  <div className="flex flex-wrap gap-1.5">
                    {POPULAR_MODULE_SOURCES.map((ps) => (
                      <Button
                        key={ps.name}
                        type="button"
                        variant="outline"
                        size="sm"
                        className="text-xs h-7"
                        onClick={() => {
                          form.setValue('name', ps.name);
                          form.setValue('url', ps.url);
                          form.setValue('branch', ps.branch);
                          form.setValue('description', ps.description);
                          form.setValue('source_type', 'git');
                        }}
                        title={ps.description}
                      >
                        {ps.name}
                      </Button>
                    ))}
                  </div>
                </div>
              )}

              <div className="grid grid-cols-2 gap-4">
                <FormField
                  control={form.control}
                  name="name"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Name *</FormLabel>
                      <FormControl>
                        <Input placeholder="my-terraform-modules" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="source_type"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Source Type *</FormLabel>
                      <Select
                        value={field.value}
                        onValueChange={field.onChange}
                        disabled={!!source}
                      >
                        <FormControl>
                          <SelectTrigger>
                            <SelectValue />
                          </SelectTrigger>
                        </FormControl>
                        <SelectContent>
                          <SelectItem value="git">Git Repository</SelectItem>
                          <SelectItem value="registry">OpenTofu Registry (Phase 2)</SelectItem>
                        </SelectContent>
                      </Select>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>

              <FormField
                control={form.control}
                name="url"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Repository URL *</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="https://github.com/username/terraform-modules.git"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {sourceType === 'git' && (
                <>
                  <div className="grid grid-cols-2 gap-4">
                    <FormField
                      control={form.control}
                      name="branch"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Branch</FormLabel>
                          <FormControl>
                            <Input placeholder="main" {...field} value={field.value ?? ''} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="git_ref"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Git Ref (Tag/Commit)</FormLabel>
                          <FormControl>
                            <Input placeholder="v1.0.0" {...field} value={field.value ?? ''} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <FormField
                      control={form.control}
                      name="auth_type"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Authentication</FormLabel>
                          <Select value={field.value} onValueChange={field.onChange}>
                            <FormControl>
                              <SelectTrigger>
                                <SelectValue />
                              </SelectTrigger>
                            </FormControl>
                            <SelectContent>
                              <SelectItem value="none">None (Public Repo)</SelectItem>
                              <SelectItem value="token">Token</SelectItem>
                              <SelectItem value="ssh" disabled>SSH Key (Coming Soon)</SelectItem>
                            </SelectContent>
                          </Select>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    {authType === 'token' && (
                      <FormField
                        control={form.control}
                        name="auth_token"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel>Personal Access Token</FormLabel>
                            <FormControl>
                              <Input
                                type="password"
                                placeholder="ghp_xxxxx"
                                {...field}
                                value={field.value ?? ''}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    )}
                  </div>
                </>
              )}

              <FormField
                control={form.control}
                name="description"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Description</FormLabel>
                    <FormControl>
                      <Textarea
                        placeholder="Describe this module source..."
                        rows={2}
                        {...field}
                        value={field.value ?? ''}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="auto_sync"
                render={({ field }) => (
                  <FormItem className="flex items-center space-x-2 space-y-0">
                    <FormControl>
                      <input
                        type="checkbox"
                        checked={field.value}
                        onChange={(e) => field.onChange(e.target.checked)}
                        className="h-4 w-4"
                      />
                    </FormControl>
                    <FormLabel className="cursor-pointer">
                      Auto-sync (every {syncInterval} hours)
                    </FormLabel>
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="make_default"
                render={({ field }) => (
                  <FormItem className="flex items-center space-x-2 space-y-0">
                    <FormControl>
                      <Checkbox
                        checked={!!field.value}
                        onCheckedChange={(checked) => field.onChange(checked === true)}
                      />
                    </FormControl>
                    <FormLabel className="cursor-pointer">Make Default</FormLabel>
                  </FormItem>
                )}
              />
            </div>

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={isSubmitting}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : null}
                {isSubmitting
                  ? source ? 'Updating...' : 'Adding...'
                  : source ? 'Update Source' : 'Add Source'}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
