import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { variableMappingSchema, type VariableMappingFormData } from '@/schemas';
import { Plus, Trash2, Edit2, FileCode, Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
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
  DialogTrigger,
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
import {
  useVariableMappings,
  useCreateVariableMapping,
  useUpdateVariableMapping,
  useDeleteVariableMapping,
  useVariableMappingTemplates,
  useApplyVariableMappingTemplate,
} from '@/hooks/useModules';
import type { VariableMapping, VariableMappingRequest } from '@/types';

interface VariableMappingsManagerProps {
  moduleId: number;
}

const TRANSFORM_TYPES = [
  { value: 'none', label: 'None' },
  { value: 'uppercase', label: 'Uppercase' },
  { value: 'lowercase', label: 'Lowercase' },
  { value: 'json_encode', label: 'JSON Encode' },
  { value: 'base64', label: 'Base64 Encode' },
];

/**
 * Variable name patterns that should never be base64-encoded.
 * JWTs and other tokens are already base64url-encoded internally —
 * double-encoding breaks signature verification (S23-001).
 */
const SENSITIVE_VAR_PATTERNS = [
  /jwt/i,
  /token/i,
  /secret/i,
  /password/i,
  /api_key/i,
  /private_key/i,
];

function isSensitiveVar(varName: string): boolean {
  return SENSITIVE_VAR_PATTERNS.some((p) => p.test(varName));
}

/**
 * Filter transform types based on the source variable name.
 * Hides "Base64 Encode" for sensitive variables to prevent accidental
 * double-encoding of JWTs and credentials (S23-001).
 */
function getAvailableTransforms(sourceVarName: string) {
  if (isSensitiveVar(sourceVarName)) {
    return TRANSFORM_TYPES.filter((t) => t.value !== 'base64');
  }
  return TRANSFORM_TYPES;
}

export function VariableMappingsManager({ moduleId }: VariableMappingsManagerProps) {
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
  const [isEditDialogOpen, setIsEditDialogOpen] = useState(false);
  const [isTemplateDialogOpen, setIsTemplateDialogOpen] = useState(false);
  const [editingMapping, setEditingMapping] = useState<VariableMapping | null>(null);

  const { data: mappingsData, isLoading } = useVariableMappings(moduleId);
  const { data: templatesData } = useVariableMappingTemplates();
  const createMapping = useCreateVariableMapping();
  const updateMapping = useUpdateVariableMapping();
  const deleteMapping = useDeleteVariableMapping();
  const applyTemplate = useApplyVariableMappingTemplate();

  const mappings = mappingsData?.mappings || [];
  const templates = templatesData?.templates || [];

  const handleCreate = (data: VariableMappingRequest) => {
    createMapping.mutate(
      { moduleId, data },
      {
        onSuccess: () => {
          setIsCreateDialogOpen(false);
        },
      }
    );
  };

  const handleUpdate = (mappingId: number, data: VariableMappingRequest) => {
    updateMapping.mutate(
      { moduleId, mappingId, data },
      {
        onSuccess: () => {
          setIsEditDialogOpen(false);
          setEditingMapping(null);
        },
      }
    );
  };

  const handleDelete = (mappingId: number) => {
    if (confirm('Are you sure you want to delete this mapping?')) {
      deleteMapping.mutate({ moduleId, mappingId });
    }
  };

  const handleApplyTemplate = (templateId: number) => {
    applyTemplate.mutate(
      { moduleId, templateId },
      {
        onSuccess: () => {
          setIsTemplateDialogOpen(false);
        },
      }
    );
  };

  if (isLoading) {
    return <div className="text-sm text-muted-foreground">Loading mappings...</div>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold">Variable Mappings</h3>
          <p className="text-sm text-muted-foreground">
            Map external variable names to internal names with transformations
          </p>
        </div>
        <div className="flex gap-2">
          <Dialog open={isTemplateDialogOpen} onOpenChange={setIsTemplateDialogOpen}>
            <DialogTrigger asChild>
              <Button variant="outline" size="sm">
                <Sparkles className="h-4 w-4 mr-2" />
                Apply Template
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Apply Mapping Template</DialogTitle>
                <DialogDescription>
                  Apply a pre-configured variable mapping template to this module
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-3">
                {templates.length === 0 ? (
                  <Alert>
                    <AlertDescription>No templates available</AlertDescription>
                  </Alert>
                ) : (
                  templates.map((template) => (
                    <div
                      key={template.id}
                      className="border rounded-lg p-3 hover:bg-muted/50 cursor-pointer"
                      onClick={() => handleApplyTemplate(template.id)}
                    >
                      <div className="flex items-start justify-between">
                        <div>
                          <div className="font-medium">{template.name}</div>
                          {template.description && (
                            <div className="text-sm text-muted-foreground mt-1">
                              {template.description}
                            </div>
                          )}
                          <div className="text-xs text-muted-foreground mt-2">
                            {template.mappings?.length || 0} mappings • Used{' '}
                            {template.usage_count || 0} times
                          </div>
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </DialogContent>
          </Dialog>

          <Dialog open={isCreateDialogOpen} onOpenChange={setIsCreateDialogOpen}>
            <DialogTrigger asChild>
              <Button size="sm">
                <Plus className="h-4 w-4 mr-2" />
                Add Mapping
              </Button>
            </DialogTrigger>
            <DialogContent>
              <MappingForm onSubmit={handleCreate} onCancel={() => setIsCreateDialogOpen(false)} />
            </DialogContent>
          </Dialog>
        </div>
      </div>

      {mappings.length === 0 ? (
        <Alert>
          <FileCode className="h-4 w-4" />
          <AlertDescription>
            No variable mappings configured. Click "Add Mapping" to create one or apply a template.
          </AlertDescription>
        </Alert>
      ) : (
        <div className="border rounded-lg">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Source Variable</TableHead>
                <TableHead>Target Variable</TableHead>
                <TableHead>Transform</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {mappings.map((mapping) => (
                <TableRow key={mapping.id}>
                  <TableCell className="font-mono text-sm">{mapping.source_variable_name}</TableCell>
                  <TableCell className="font-mono text-sm">{mapping.target_variable_name}</TableCell>
                  <TableCell>
                    <Badge variant="outline">
                      {TRANSFORM_TYPES.find((t) => t.value === mapping.transform_type)?.label ||
                        mapping.transform_type}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    {mapping.is_active ? (
                      <Badge variant="default">Active</Badge>
                    ) : (
                      <Badge variant="secondary">Inactive</Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setEditingMapping(mapping);
                          setIsEditDialogOpen(true);
                        }}
                      >
                        <Edit2 className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleDelete(mapping.id)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {editingMapping && (
        <Dialog open={isEditDialogOpen} onOpenChange={setIsEditDialogOpen}>
          <DialogContent>
            <MappingForm
              initialData={editingMapping}
              onSubmit={(data) => handleUpdate(editingMapping.id, data)}
              onCancel={() => {
                setIsEditDialogOpen(false);
                setEditingMapping(null);
              }}
            />
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}

interface MappingFormProps {
  initialData?: VariableMapping;
  onSubmit: (data: VariableMappingRequest) => void;
  onCancel: () => void;
}

function MappingForm({ initialData, onSubmit, onCancel }: MappingFormProps) {
  const form = useForm<VariableMappingFormData>({
    resolver: zodResolver(variableMappingSchema),
    defaultValues: {
      source_variable_name: initialData?.source_variable_name || '',
      target_variable_name: initialData?.target_variable_name || '',
      transform_type: initialData?.transform_type || 'none',
      is_active: initialData?.is_active ?? true,
      description: initialData?.description || '',
    },
  });

  const sourceName = form.watch('source_variable_name');
  const transformType = form.watch('transform_type');

  // S23-001: When the source name changes to a sensitive variable, drop a
  // base64 transform so we don't double-encode tokens/secrets.
  useEffect(() => {
    if (transformType === 'base64' && isSensitiveVar(sourceName)) {
      form.setValue('transform_type', 'none', { shouldDirty: true });
    }
  }, [sourceName, transformType, form]);

  const onValid = (values: VariableMappingFormData) => {
    onSubmit(values as VariableMappingRequest);
  };

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onValid)} noValidate>
        <DialogHeader>
          <DialogTitle>{initialData ? 'Edit Mapping' : 'Create Mapping'}</DialogTitle>
          <DialogDescription>
            Map an external variable name to an internal name with optional transformation
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          <FormField
            control={form.control}
            name="source_variable_name"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Source Variable Name</FormLabel>
                <FormControl>
                  <Input placeholder="vpc_id" {...field} />
                </FormControl>
                <FormDescription>
                  The external/original variable name from the source
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="target_variable_name"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Target Variable Name</FormLabel>
                <FormControl>
                  <Input placeholder="vpc_id" {...field} />
                </FormControl>
                <FormDescription>
                  The internal/mapped variable name to use
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="transform_type"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Transformation</FormLabel>
                <Select value={field.value} onValueChange={field.onChange}>
                  <FormControl>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    {getAvailableTransforms(sourceName).map((type) => (
                      <SelectItem key={type.value} value={type.value}>
                        {type.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {isSensitiveVar(sourceName) ? (
                  <p className="text-xs text-warning">
                    Base64 Encode is hidden for sensitive variables (tokens, secrets, keys)
                    to prevent double-encoding.
                  </p>
                ) : (
                  <FormDescription>
                    Optional transformation to apply to the variable value
                  </FormDescription>
                )}
                <FormMessage />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="description"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Description (Optional)</FormLabel>
                <FormControl>
                  <Textarea
                    placeholder="Describe what this mapping does..."
                    rows={3}
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
            name="is_active"
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
                <FormLabel className="cursor-pointer">Active</FormLabel>
              </FormItem>
            )}
          />
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={onCancel}>
            Cancel
          </Button>
          <Button type="submit">{initialData ? 'Update' : 'Create'}</Button>
        </DialogFooter>
      </form>
    </Form>
  );
}
