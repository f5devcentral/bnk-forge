import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Cloud, Lock, CheckCircle2, AlertCircle, Loader2, ExternalLink, Edit, Trash2 } from 'lucide-react';
import type { Project } from '@/types';
import { useUpdateProject } from '@/hooks/useProjects';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { notify, notifyError } from '@/lib/notify';


interface CloudCredentialsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project: Project;
}

export function CloudCredentialsDialog({
  open,
  onOpenChange,
  project,
}: CloudCredentialsDialogProps) {
  const navigate = useNavigate();
  const updateProjectMutation = useUpdateProject();
  const [selectedTemplateId, setSelectedTemplateId] = useState<number | undefined>(
    project.credential_template_id
  );
  const [isEditing, setIsEditing] = useState(!project.credential_template_id);

  // Fetch credential templates
  const { data: templates, isLoading: templatesLoading } = useQuery({
    queryKey: ['credential-templates'],
    queryFn: () => api.listCredentialTemplates(),
  });

  // Update selected template when project changes
  useEffect(() => {
    setSelectedTemplateId(project.credential_template_id);
    setIsEditing(!project.credential_template_id);
  }, [project.credential_template_id]);

  const handleSave = async () => {
    try {
      await updateProjectMutation.mutateAsync({
        id: project.id,
        data: {
          credential_template_id: selectedTemplateId === 0 ? undefined : selectedTemplateId,
        },
      });

      notify.success('Credential template assigned successfully', undefined, { category: 'credentials' });
      setIsEditing(false);
      onOpenChange(false);
    } catch (error) {
      notifyError(error);
    }
  };

  const handleRemove = async () => {
    try {
      await updateProjectMutation.mutateAsync({
        id: project.id,
        data: {
          credential_template_id: undefined,
        },
      });

      notify.success('Credential template removed', undefined, { category: 'credentials' });
      setSelectedTemplateId(undefined);
      setIsEditing(true);
    } catch (error) {
      notifyError(error);
    }
  };

  const selectedTemplate = templates?.find((t) => t.id === selectedTemplateId);
  const currentTemplate = templates?.find((t) => t.id === project.credential_template_id);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Cloud className="h-5 w-5" />
            Cloud Provider Credentials
          </DialogTitle>
          <DialogDescription>
            {currentTemplate && !isEditing
              ? `Current credentials for project: ${project.name}`
              : `Select a credential template for project: ${project.name}`
            }
          </DialogDescription>
        </DialogHeader>

        <Alert>
          <Lock className="h-4 w-4" />
          <AlertDescription>
            Credential templates are created and managed in Settings. {currentTemplate && !isEditing ? 'Click Edit to change.' : 'Select one to use for this project\'s deployments.'}
          </AlertDescription>
        </Alert>

        {/* Show current template with edit/delete when not editing */}
        {currentTemplate && !isEditing ? (
          <Card className="border-l-4 border-l-success">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm flex items-center gap-2 text-foreground">
                <CheckCircle2 className="h-4 w-4 text-success" />
                Active Credential Template
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <p className="text-xs text-muted-foreground font-medium">Template</p>
                  <p className="text-foreground font-semibold">{currentTemplate.name}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground font-medium">Provider</p>
                  <p className="text-foreground uppercase">{currentTemplate.provider}</p>
                </div>
                {currentTemplate.region && (
                  <div>
                    <p className="text-xs text-muted-foreground font-medium">Region</p>
                    <p className="text-foreground">{currentTemplate.region}</p>
                  </div>
                )}
                {currentTemplate.aws_auth_method && (
                  <div>
                    <p className="text-xs text-muted-foreground font-medium">Auth Method</p>
                    <p className="text-foreground">
                      {currentTemplate.aws_auth_method === 'access_keys' && 'Access Keys'}
                      {currentTemplate.aws_auth_method === 'profile' && `Profile: ${currentTemplate.aws_profile}`}
                      {currentTemplate.aws_sso_enabled && 'AWS SSO'}
                    </p>
                  </div>
                )}
              </div>
              {currentTemplate.description && (
                <p className="text-xs text-muted-foreground italic">
                  {currentTemplate.description}
                </p>
              )}

              <div className="flex gap-2 pt-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setIsEditing(true)}
                  className="flex-1"
                >
                  <Edit className="h-4 w-4 mr-2" />
                  Change Template
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={handleRemove}
                  disabled={updateProjectMutation.isPending}
                  className="flex-1"
                >
                  {updateProjectMutation.isPending ? (
                    <>
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                      Removing...
                    </>
                  ) : (
                    <>
                      <Trash2 className="h-4 w-4 mr-2" />
                      Remove
                    </>
                  )}
                </Button>
              </div>
            </CardContent>
          </Card>
        ) : (
          <>
            {/* Show warning when no template and not selected */}
            {!currentTemplate && !selectedTemplate && (
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>
                  No credentials configured. Select a template below to enable cloud deployments for this project.
                </AlertDescription>
              </Alert>
            )}

            {/* Template Selection Form */}
            <Card>
              <CardHeader>
                <CardTitle className="text-lg">Select Credential Template</CardTitle>
                <CardDescription>
                  Choose a credential template to use for this project's OpenTofu deployments
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {templatesLoading ? (
                  <div className="flex items-center justify-center py-8">
                    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                  </div>
                ) : (
                  <>
                    <div className="grid gap-2">
                      <Label htmlFor="credential-template">Credential Template</Label>
                      <Select
                        value={selectedTemplateId?.toString() || '0'}
                        onValueChange={(value) => setSelectedTemplateId(value === '0' ? undefined : parseInt(value))}
                      >
                        <SelectTrigger>
                          <SelectValue placeholder="Select a credential template" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="0">
                            <span className="text-muted-foreground">No template (remove credentials)</span>
                          </SelectItem>
                          {templates && templates.length > 0 ? (
                            templates.map((template) => (
                              <SelectItem key={template.id} value={template.id.toString()}>
                                <div className="flex items-center gap-2">
                                  <span>{template.name}</span>
                                  <span className="text-xs text-muted-foreground">
                                    ({template.provider.toUpperCase()})
                                  </span>
                                  {template.is_default && (
                                    <span className="text-xs bg-info/10 text-info border border-info/20 px-1.5 py-0.5 rounded">
                                      Default
                                    </span>
                                  )}
                                </div>
                              </SelectItem>
                            ))
                          ) : (
                            <SelectItem value="none" disabled>
                              No templates available
                            </SelectItem>
                          )}
                        </SelectContent>
                      </Select>
                      <p className="text-xs text-muted-foreground">
                        Templates are configured in Settings → Cloud Providers
                      </p>
                    </div>

                    {/* Selected Template Preview */}
                    {selectedTemplate && (
                      <div className="rounded-lg border border-l-4 border-l-info bg-card p-4">
                        <h4 className="text-sm font-medium text-foreground mb-2">Selected Template Details</h4>
                        <div className="grid grid-cols-2 gap-3 text-sm">
                          <div>
                            <p className="text-xs text-muted-foreground font-medium">Name</p>
                            <p className="text-foreground">{selectedTemplate.name}</p>
                          </div>
                          <div>
                            <p className="text-xs text-muted-foreground font-medium">Provider</p>
                            <p className="text-foreground uppercase">{selectedTemplate.provider}</p>
                          </div>
                          {selectedTemplate.region && (
                            <div>
                              <p className="text-xs text-muted-foreground font-medium">Region</p>
                              <p className="text-foreground">{selectedTemplate.region}</p>
                            </div>
                          )}
                          {selectedTemplate.aws_auth_method && (
                            <div>
                              <p className="text-xs text-muted-foreground font-medium">Auth Method</p>
                              <p className="text-foreground">
                                {selectedTemplate.aws_auth_method === 'access_keys' && 'Access Keys'}
                                {selectedTemplate.aws_auth_method === 'profile' && `Profile: ${selectedTemplate.aws_profile}`}
                                {selectedTemplate.aws_sso_enabled && 'AWS SSO'}
                              </p>
                            </div>
                          )}
                        </div>
                        {selectedTemplate.description && (
                          <p className="text-xs text-muted-foreground italic mt-3">
                            {selectedTemplate.description}
                          </p>
                        )}
                      </div>
                    )}

                    {!templates || templates.length === 0 ? (
                      <Alert>
                        <AlertCircle className="h-4 w-4" />
                        <AlertDescription>
                          <p className="mb-2">No credential templates found. Create one in Settings first.</p>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => {
                              onOpenChange(false);
                              navigate('/settings?tab=cloud');
                            }}
                          >
                            <ExternalLink className="h-3 w-3 mr-1" />
                            Go to Settings
                          </Button>
                        </AlertDescription>
                      </Alert>
                    ) : null}

                    <div className="flex gap-2 pt-4">
                      <Button
                        variant="outline"
                        onClick={() => {
                          if (currentTemplate) {
                            setIsEditing(false);
                          } else {
                            onOpenChange(false);
                          }
                        }}
                        disabled={updateProjectMutation.isPending}
                      >
                        Cancel
                      </Button>
                      <Button
                        onClick={handleSave}
                        disabled={updateProjectMutation.isPending}
                        className="flex-1"
                      >
                        {updateProjectMutation.isPending ? (
                          <>
                            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                            Saving...
                          </>
                        ) : (
                          'Save Credentials'
                        )}
                      </Button>
                    </div>
                  </>
                )}
              </CardContent>
            </Card>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
