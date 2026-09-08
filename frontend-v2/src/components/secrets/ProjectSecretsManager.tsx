/**
 * Project Secrets Manager
 *
 * Reusable component for managing project secrets (files and values).
 * Can be used in:
 * - Stack wizard (for required secrets)
 * - Project settings (full management)
 *
 * D-020: token-pure surfaces (`bg-card`, `border-border`, `text-muted-foreground`)
 * and semantic status colors (`text-success`/`text-warning`/`text-destructive`).
 * No `useTheme` plumbing.
 */

import { useState, useRef, memo } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Textarea } from '@/components/ui/textarea';
import { SectionCard } from '@/components/ui/section-card';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  useProjectSecrets,
  useCreateFileSecret,
  useCreateValueSecret,
  useImportSpecialSecret,
  useDeleteProjectSecret,
  useUpdateStackVariableSecret,
  useDeleteStackVariableSecret,
} from '@/hooks/useProjectSecrets';
import { cn, formatBytes } from '@/lib/utils';
import {
  Upload,
  Key,
  FileText,
  Trash2,
  MoreVertical,
  Plus,
  CheckCircle,
  AlertCircle,
  Shield,
  Eye,
  EyeOff,
  Link2,
  Pencil,
} from 'lucide-react';
import type { ProjectSecret, RequiredSecret } from '@/types';

interface ProjectSecretsManagerProps {
  projectId: number;
  /** If provided, shows only secrets relevant to this stack and highlights missing ones */
  requiredSecrets?: RequiredSecret[];
  /** Compact mode for embedding in dialogs */
  compact?: boolean;
  /** Called when secrets change (for parent components to react) */
  onSecretsChange?: () => void;
}

export function ProjectSecretsManager({
  projectId,
  requiredSecrets,
  compact = false,
  onSecretsChange,
}: ProjectSecretsManagerProps) {
  const { data: secretsData, isLoading } = useProjectSecrets(projectId);
  const createFile = useCreateFileSecret(projectId);
  const createValue = useCreateValueSecret(projectId);
  const importSpecialSecret = useImportSpecialSecret(projectId);
  const deleteSecret = useDeleteProjectSecret(projectId);
  const updateStackVariable = useUpdateStackVariableSecret(projectId);
  const deleteStackVariable = useDeleteStackVariableSecret(projectId);

  const [showAddDialog, setShowAddDialog] = useState(false);
  const [addType, setAddType] = useState<'file' | 'value'>('file');
  const [showPassword, setShowPassword] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const farImportInputRef = useRef<HTMLInputElement>(null);
  const jwtImportInputRef = useRef<HTMLInputElement>(null);

  // Edit stack variable dialog state
  const [showEditStackVarDialog, setShowEditStackVarDialog] = useState(false);
  const [editingStackVar, setEditingStackVar] = useState<RequiredSecret | null>(null);
  const [editStackVarValue, setEditStackVarValue] = useState('');

  // Form state
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [value, setValue] = useState('');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [targetModulePath, setTargetModulePath] = useState('');
  const [targetVariableName, setTargetVariableName] = useState('');

  const secrets = secretsData?.secrets || [];

  // Check which required secrets are satisfied
  const getMissingSecrets = () => {
    if (!requiredSecrets) return [];
    return requiredSecrets.filter(rs => !rs.exists);
  };

  const missingSecrets = getMissingSecrets();

  const resetForm = () => {
    setName('');
    setDescription('');
    setValue('');
    setSelectedFile(null);
    setTargetModulePath('');
    setTargetVariableName('');
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      setSelectedFile(file);
      if (!name) {
        // Auto-generate name from filename
        setName(file.name.replace(/\.[^/.]+$/, '').replace(/[^a-zA-Z0-9-_]/g, '-'));
      }
    }
  };

  const handleCreate = async () => {
    try {
      if (addType === 'file' && selectedFile) {
        await createFile.mutateAsync({
          file: selectedFile,
          name,
          description: description || undefined,
          targetModulePath: targetModulePath || undefined,
          targetVariableName: targetVariableName || undefined,
        });
      } else if (addType === 'value' && value) {
        await createValue.mutateAsync({
          name,
          value,
          description: description || undefined,
          target_module_path: targetModulePath || undefined,
          target_variable_name: targetVariableName || undefined,
        });
      }
      resetForm();
      setShowAddDialog(false);
      onSecretsChange?.();
    } catch {
      // Error notification already handled by mutation onError callback
    }
  };

  const handleDelete = async (secretId: number) => {
    try {
      await deleteSecret.mutateAsync(secretId);
      onSecretsChange?.();
    } catch {
      // Error notification already handled by mutation onError callback
    }
  };

  const openEditStackVarDialog = (rs: RequiredSecret) => {
    setEditingStackVar(rs);
    setEditStackVarValue('');
    setShowEditStackVarDialog(true);
  };

  const handleUpdateStackVariable = async () => {
    if (!editingStackVar || !editStackVarValue) return;
    try {
      await updateStackVariable.mutateAsync({
        name: editingStackVar.variable_name,
        value: editStackVarValue,
      });
      setShowEditStackVarDialog(false);
      setEditingStackVar(null);
      onSecretsChange?.();
    } catch {
      // handled by mutation
    }
  };

  const handleDeleteStackVariable = async (variableName: string) => {
    try {
      await deleteStackVariable.mutateAsync(variableName);
      onSecretsChange?.();
    } catch {
      // handled by mutation
    }
  };

  const handleImportSpecialSecret = async (
    secretName: 'cne_pull_secret' | 'jwt_token',
    file: File,
  ) => {
    try {
      await importSpecialSecret.mutateAsync({ secretName, file });
      onSecretsChange?.();
    } catch {
      // handled by mutation
    }
  };

  const isSpecialImportSecret = (variableName: string) =>
    variableName === 'cne_pull_secret' || variableName === 'jwt_token';

  const openAddDialog = (type: 'file' | 'value', preset?: RequiredSecret) => {
    setAddType(type);
    if (preset) {
      setName(preset.variable_name);
      setDescription(preset.description || '');
      setTargetModulePath(preset.module_path);
      setTargetVariableName(preset.variable_name);
    }
    setShowAddDialog(true);
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-primary" />
      </div>
    );
  }

  return (
    <div className={cn('space-y-4', compact && 'space-y-3')}>
      {/* Missing secrets alert */}
      {requiredSecrets && missingSecrets.length > 0 && (
        <Alert className="border-warning/30 bg-warning/10">
          <AlertCircle className="h-4 w-4 text-warning" />
          <AlertDescription className="text-sm">
            <strong>{missingSecrets.length} secret{missingSecrets.length > 1 ? 's' : ''} required.</strong>{' '}
            Upload or configure before deploying.
          </AlertDescription>
        </Alert>
      )}

      {/* Quick import helpers for BNK secrets */}
      <SectionCard title="Quick import (BNK)" compact>
        <p className="text-xs text-muted-foreground mb-3">
          Upload FAR/JWT files directly. Forge normalizes and validates them before saving as encrypted value secrets.
        </p>
        <div className="flex flex-wrap gap-2">
          <input
            ref={farImportInputRef}
            type="file"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void handleImportSpecialSecret('cne_pull_secret', f);
              e.currentTarget.value = '';
            }}
          />
          <Button
            size="sm"
            variant="outline"
            onClick={() => farImportInputRef.current?.click()}
            disabled={importSpecialSecret.isPending}
          >
            Import FAR Secret File
          </Button>

          <input
            ref={jwtImportInputRef}
            type="file"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void handleImportSpecialSecret('jwt_token', f);
              e.currentTarget.value = '';
            }}
          />
          <Button
            size="sm"
            variant="outline"
            onClick={() => jwtImportInputRef.current?.click()}
            disabled={importSpecialSecret.isPending}
          >
            Import JWT File
          </Button>
        </div>
      </SectionCard>

      {/* Required secrets (if in stack wizard mode) */}
      {requiredSecrets && requiredSecrets.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-sm font-medium text-foreground">
            <Shield className="h-4 w-4 text-primary" />
            Required Secrets
          </div>
          <div className="space-y-2">
            {requiredSecrets.map((rs) => (
              <div
                key={`${rs.module_path}-${rs.variable_name}`}
                className={cn(
                  'flex items-center justify-between p-3 rounded-lg border bg-card',
                  rs.exists ? 'border-success/30' : 'border-warning/30',
                )}
              >
                <div className="flex items-center gap-3">
                  {rs.type === 'file' ? (
                    <FileText className={cn('h-4 w-4', rs.exists ? 'text-success' : 'text-warning')} />
                  ) : (
                    <Key className={cn('h-4 w-4', rs.exists ? 'text-success' : 'text-warning')} />
                  )}
                  <div>
                    <div className="text-sm font-medium text-foreground">{rs.variable_name}</div>
                    <div className="text-xs text-muted-foreground">
                      {rs.description || `Required for ${rs.module_path.split('/').pop()}`}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {rs.exists ? (
                    <>
                      <Badge variant="success">
                        <CheckCircle className="h-3 w-3 mr-1" />
                        Configured
                      </Badge>
                      {rs.source === 'stack_variable' && (
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="sm" className="h-7 w-7 p-0" aria-label="Secret actions">
                              <MoreVertical className="h-3.5 w-3.5" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem onClick={() => openEditStackVarDialog(rs)}>
                              <Pencil className="h-3.5 w-3.5 mr-2" />
                              Edit
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              onClick={() => handleDeleteStackVariable(rs.variable_name)}
                              className="text-destructive focus:text-destructive"
                            >
                              <Trash2 className="h-3.5 w-3.5 mr-2" />
                              Remove
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      )}
                    </>
                  ) : (
                    <div className="flex items-center gap-2">
                      {isSpecialImportSecret(rs.variable_name) ? (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => {
                            if (rs.variable_name === 'cne_pull_secret') {
                              farImportInputRef.current?.click();
                            } else {
                              jwtImportInputRef.current?.click();
                            }
                          }}
                          className="h-7 text-xs"
                        >
                          <Upload className="h-3 w-3 mr-1" />
                          Import File
                        </Button>
                      ) : null}
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => openAddDialog(rs.type, rs)}
                        className="h-7 text-xs"
                      >
                        <Plus className="h-3 w-3 mr-1" />
                        Add {rs.type === 'file' ? 'File' : 'Value'}
                      </Button>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Existing secrets list */}
      {secrets.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-medium text-foreground">
              <Shield className="h-4 w-4 text-primary" />
              Project Secrets
              <Badge variant="secondary" className="text-xs">{secrets.length}</Badge>
            </div>
            {(!requiredSecrets || requiredSecrets.length === 0) && (
              <Button size="sm" variant="outline" onClick={() => openAddDialog('file')}>
                <Plus className="h-3 w-3 mr-1" />
                Add Secret
              </Button>
            )}
          </div>
          <div className="space-y-2">
            {secrets.map((secret) => (
              <SecretItem
                key={secret.id}
                secret={secret}
                onDelete={() => handleDelete(secret.id)}
              />
            ))}
          </div>
        </div>
      )}

      {/* Empty state */}
      {secrets.length === 0 && (!requiredSecrets || requiredSecrets.length === 0) && (
        <div className="text-center py-8 rounded-lg border border-dashed border-border">
          <Shield className="h-10 w-10 mx-auto mb-2 text-muted-foreground opacity-50" />
          <p className="text-sm text-muted-foreground mb-3">No secrets configured</p>
          <div className="flex justify-center gap-2">
            <Button size="sm" variant="outline" onClick={() => openAddDialog('file')}>
              <Upload className="h-3 w-3 mr-1" />
              Upload File
            </Button>
            <Button size="sm" variant="outline" onClick={() => openAddDialog('value')}>
              <Key className="h-3 w-3 mr-1" />
              Add Value
            </Button>
          </div>
        </div>
      )}

      {/* Add buttons (when we have secrets but not in required mode) */}
      {secrets.length > 0 && (!requiredSecrets || requiredSecrets.length === 0) && (
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={() => openAddDialog('file')}>
            <Upload className="h-3 w-3 mr-1" />
            Upload File
          </Button>
          <Button size="sm" variant="outline" onClick={() => openAddDialog('value')}>
            <Key className="h-3 w-3 mr-1" />
            Add Value
          </Button>
        </div>
      )}

      {/* Add Secret Dialog */}
      <Dialog open={showAddDialog} onOpenChange={setShowAddDialog}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {addType === 'file' ? (
                <>
                  <Upload className="h-5 w-5 text-primary" />
                  Upload File Secret
                </>
              ) : (
                <>
                  <Key className="h-5 w-5 text-primary" />
                  Add Value Secret
                </>
              )}
            </DialogTitle>
            <DialogDescription>
              {addType === 'file'
                ? 'Upload a credentials file (JSON, PEM, etc.) that will be encrypted and made available to modules at runtime.'
                : 'Add a secret value (API key, password, etc.) that will be encrypted and injected into module variables.'}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 pt-2">
            {/* File upload */}
            {addType === 'file' && (
              <div className="space-y-2">
                <Label>File *</Label>
                <input
                  ref={fileInputRef}
                  type="file"
                  onChange={handleFileSelect}
                  className="hidden"
                />
                <Button
                  type="button"
                  variant="outline"
                  className="w-full h-20 border-dashed"
                  onClick={() => fileInputRef.current?.click()}
                >
                  {selectedFile ? (
                    <div className="text-center">
                      <FileText className="h-6 w-6 mx-auto mb-1 text-success" />
                      <p className="text-sm font-medium">{selectedFile.name}</p>
                      <p className="text-xs text-muted-foreground">{formatBytes(selectedFile.size)}</p>
                    </div>
                  ) : (
                    <div className="text-center">
                      <Upload className="h-6 w-6 mx-auto mb-1 text-muted-foreground" />
                      <p className="text-sm">Click to select file</p>
                    </div>
                  )}
                </Button>
              </div>
            )}

            {/* Name */}
            <div className="space-y-2">
              <Label htmlFor="secret-name">Name *</Label>
              <Input
                id="secret-name"
                placeholder="e.g., far-credentials"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>

            {/* Value (for value secrets) */}
            {addType === 'value' && (
              <div className="space-y-2">
                <Label htmlFor="secret-value">Value *</Label>
                <div className="relative">
                  <Textarea
                    id="secret-value"
                    placeholder="Enter secret value..."
                    value={value}
                    onChange={(e) => setValue(e.target.value)}
                    className={cn('pr-10', !showPassword && 'font-mono text-xs')}
                    style={!showPassword ? { WebkitTextSecurity: 'disc' } as React.CSSProperties : undefined}
                    rows={3}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="absolute right-1 top-1"
                    onClick={() => setShowPassword(!showPassword)}
                  >
                    {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </Button>
                </div>
              </div>
            )}

            {/* Description */}
            <div className="space-y-2">
              <Label htmlFor="secret-desc">Description</Label>
              <Input
                id="secret-desc"
                placeholder="Optional description..."
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>

            {/* Target mapping */}
            <div className="space-y-2">
              <Label className="flex items-center gap-1">
                <Link2 className="h-3 w-3" />
                Target Variable
              </Label>
              <Input
                placeholder="e.g., service_account_key_file"
                value={targetVariableName}
                onChange={(e) => setTargetVariableName(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                The module variable this secret will be injected as.
              </p>
            </div>

            {/* Actions */}
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => setShowAddDialog(false)}>
                Cancel
              </Button>
              <Button
                onClick={handleCreate}
                disabled={
                  !name ||
                  (addType === 'file' && !selectedFile) ||
                  (addType === 'value' && !value) ||
                  createFile.isPending ||
                  createValue.isPending
                }
              >
                {(createFile.isPending || createValue.isPending) ? 'Creating...' : 'Create Secret'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Edit Stack Variable Secret Dialog */}
      <Dialog open={showEditStackVarDialog} onOpenChange={setShowEditStackVarDialog}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Pencil className="h-4 w-4" />
              Edit Secret: {editingStackVar?.variable_name}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            {editingStackVar?.description && (
              <p className="text-sm text-muted-foreground">{editingStackVar.description}</p>
            )}
            <div className="space-y-2">
              <Label>New Value</Label>
              <div className="relative">
                <Input
                  type={showPassword ? 'text' : 'password'}
                  placeholder="Enter new value"
                  value={editStackVarValue}
                  onChange={(e) => setEditStackVarValue(e.target.value)}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="absolute right-1 top-1/2 -translate-y-1/2 h-7 w-7 p-0"
                  onClick={() => setShowPassword(!showPassword)}
                >
                  {showPassword ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                </Button>
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => setShowEditStackVarDialog(false)}>
                Cancel
              </Button>
              <Button
                onClick={handleUpdateStackVariable}
                disabled={!editStackVarValue || updateStackVariable.isPending}
              >
                {updateStackVariable.isPending ? 'Saving...' : 'Save'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// Secret item component - wrapped with memo to prevent unnecessary re-renders
const SecretItem = memo(function SecretItem({
  secret,
  onDelete,
}: {
  secret: ProjectSecret;
  onDelete: () => void;
}) {
  return (
    <div className="flex items-center justify-between p-3 rounded-lg border border-border bg-muted/50">
      <div className="flex items-center gap-3">
        {secret.secret_type === 'file' ? (
          <FileText className="h-4 w-4 text-info" />
        ) : (
          <Key className="h-4 w-4 text-warning" />
        )}
        <div>
          <div className="text-sm font-medium text-foreground">{secret.name}</div>
          <div className="text-xs text-muted-foreground">
            {secret.secret_type === 'file' && secret.filename && (
              <span>{secret.filename} ({formatBytes(secret.file_size || 0)})</span>
            )}
            {secret.secret_type === 'value' && <span>Encrypted value</span>}
            {secret.target_variable_name && (
              <span className="ml-2 text-primary">
                → {secret.target_variable_name}
              </span>
            )}
          </div>
        </div>
      </div>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="sm" className="h-8 w-8 p-0" aria-label="Secret actions">
            <MoreVertical className="h-4 w-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem
            onClick={onDelete}
            className="text-destructive focus:text-destructive"
          >
            <Trash2 className="h-4 w-4 mr-2" />
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
});
