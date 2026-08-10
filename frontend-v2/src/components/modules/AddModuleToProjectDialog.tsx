import { useState, useEffect, useCallback, useMemo } from 'react';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { ModuleVariableEditor } from './ModuleVariableEditor';
import { useProjects } from '@/hooks/useProjects';
import { useAddModuleToProject, useModuleLibrary, useProjectModules } from '@/hooks/useModules';
import { ChevronDown, Package, FolderGit2, CheckCircle2, Loader2, Search, AlertCircle, Globe, Cloud } from 'lucide-react';
import type { CloudCredentialTemplate, ModuleLibrary, ModuleVariable, Project, JsonValue } from '@/types';
import { notify } from '@/lib/notify';
import { Input } from '@/components/ui/input';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { api } from '@/lib/api';
import { formatModuleVariableValue, getSensitiveModuleVariableNames } from '@/lib/module-variables';
import { RegistryBrowseDialog } from './RegistryBrowseDialog';
import { PublicGitImportDialog } from './PublicGitImportDialog';
import { TFCImportDialog } from './TFCImportDialog';

interface AddModuleToProjectDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    module: ModuleLibrary | null;
    projectId?: number;
}

export function AddModuleToProjectDialog({
    open,
    onOpenChange,
    module,
    projectId,
}: AddModuleToProjectDialogProps) {
    const { data: projects } = useProjects();
    const { data: moduleLibrary } = useModuleLibrary();
    const addModuleMutation = useAddModuleToProject();
    const [selectedProjectId, setSelectedProjectId] = useState<number | null>(projectId || null);
    const [selectedModule, setSelectedModule] = useState<ModuleLibrary | null>(module);
    const [moduleSearch, setModuleSearch] = useState('');
    const [variables, setVariables] = useState<Record<string, JsonValue>>({});
    const [pathInProject, setPathInProject] = useState<string>('');
    const [step, setStep] = useState<'select' | 'configure' | 'confirm'>('select');
    const [isAdding, setIsAdding] = useState(false);
    const [missingDependencies, setMissingDependencies] = useState<ModuleLibrary[]>([]);
    const [showDependencyPrompt, setShowDependencyPrompt] = useState(false);
    const [showRegistryBrowse, setShowRegistryBrowse] = useState(false);
    const [showGitImport, setShowGitImport] = useState(false);
    const [showTFCImport, setShowTFCImport] = useState(false);
    const [pendingImportedModuleId, setPendingImportedModuleId] = useState<number | null>(null);

    const handleRegistryImported = (m: ModuleLibrary) => {
        setPendingImportedModuleId(m.id);
    };

    // Fetch project modules if a project is selected
    const { data: projectModules } = useProjectModules(selectedProjectId || 0, {
        pollingEnabled: false
    });

    const selectedProject = projects?.find((p) => p.id === selectedProjectId);
    const sensitiveVariableNames = useMemo(
        () => getSensitiveModuleVariableNames(selectedModule),
        [selectedModule]
    );

    const selectedCredentialTemplate = useMemo<CloudCredentialTemplate | undefined>(() => {
        if (!selectedProject?.credential_template_id || !selectedProject.credential_template?.provider) {
            return undefined;
        }

        if (selectedProject.credential_template.provider.toLowerCase() !== 'ibm') {
            return undefined;
        }

        return selectedProject.credential_template as CloudCredentialTemplate;
    }, [selectedProject]);

    const getProjectInheritedVariable = useCallback((project: Project | undefined, variableName: string): JsonValue | undefined => {
        if (!project || (project.cloud_provider || '').toLowerCase() !== 'ibm') {
            return undefined;
        }

        if (variableName === 'ibmcloud_cluster_region' && project.region) {
            return project.region;
        }

        if (variableName === 'ibmcloud_resource_group' && selectedCredentialTemplate?.ibmcloud_resource_group) {
            return selectedCredentialTemplate.ibmcloud_resource_group;
        }

        if (variableName === 'ibmcloud_api_key' && selectedCredentialTemplate?.has_ibmcloud_api_key) {
            return undefined;
        }

        return undefined;
    }, [selectedCredentialTemplate]);

    const formatReviewValue = useCallback(
        (key: string, value: JsonValue) => formatModuleVariableValue(key, value, sensitiveVariableNames),
        [sensitiveVariableNames]
    );

    // Initialize variables state with smart defaults from backend
    const initializeDefaultVariables = useCallback(async (mod: ModuleLibrary) => {
        if (!selectedProjectId) return;

        try {
            // Fetch smart defaults from backend
            const smartDefaults = await api.getSmartDefaults(mod.id, selectedProjectId);

            // Merge with any existing defaults from module metadata
            const defaultValues: Record<string, JsonValue> = { ...smartDefaults };

            if (mod.variables && Array.isArray(mod.variables)) {
                mod.variables.forEach((variable) => {
                    const inheritedValue = getProjectInheritedVariable(selectedProject, variable.name);
                    if (inheritedValue !== undefined && defaultValues[variable.name] === undefined) {
                        defaultValues[variable.name] = inheritedValue;
                    }

                    // Only include user-sourced variables
                    // Skip module-sourced (auto-wired) and project-sourced variables
                    const varWithSource = variable as ModuleVariable & { source?: string };
                    if ((!varWithSource.source || varWithSource.source === 'user') && variable.default !== undefined) {
                        // Don't override smart defaults
                        if (defaultValues[variable.name] === undefined) {
                            defaultValues[variable.name] = variable.default;
                        }
                    }
                });
            }

            setVariables(defaultValues);
        } catch {
            // Fallback to basic defaults
            const defaultValues: Record<string, JsonValue> = {};
            if (mod.variables && Array.isArray(mod.variables)) {
                mod.variables.forEach((variable) => {
                    const inheritedValue = getProjectInheritedVariable(selectedProject, variable.name);
                    if (inheritedValue !== undefined && defaultValues[variable.name] === undefined) {
                        defaultValues[variable.name] = inheritedValue;
                    }

                    const varWithSource = variable as ModuleVariable & { source?: string };
                    if ((!varWithSource.source || varWithSource.source === 'user') && variable.default !== undefined) {
                        if (defaultValues[variable.name] === undefined) {
                            defaultValues[variable.name] = variable.default;
                        }
                    }
                });
            }
            setVariables(defaultValues);
        }
    }, [getProjectInheritedVariable, selectedProject, selectedProjectId]);

    // Update selectedModule when module prop changes (e.g., dialog reopens with new module)
    useEffect(() => {
        if (module) {
            setSelectedModule(module);
            // Initialize variables with default values
            initializeDefaultVariables(module);
        }
        if (projectId) {
            setSelectedProjectId(projectId);
        }
    }, [module, projectId, initializeDefaultVariables]);

    // Auto-select a freshly imported registry/git/tfc module once moduleLibrary refreshes.
    useEffect(() => {
        if (!pendingImportedModuleId || !moduleLibrary) return;
        const imported = moduleLibrary.find((m: ModuleLibrary) => m.id === pendingImportedModuleId);
        if (imported) {
            setSelectedModule(imported);
            initializeDefaultVariables(imported);
            setPendingImportedModuleId(null);
        }
    }, [pendingImportedModuleId, moduleLibrary, initializeDefaultVariables]);

    const filteredModules = moduleLibrary?.filter((m: ModuleLibrary) =>
        m.name.toLowerCase().includes(moduleSearch.toLowerCase()) ||
        m.description?.toLowerCase().includes(moduleSearch.toLowerCase())
    ) || [];

    // Check for missing dependencies
    const checkMissingDependencies = () => {
        if (!selectedModule || !moduleLibrary || !projectModules) return [];

        const required = selectedModule.dependencies_metadata?.required || [];
        if (required.length === 0) return [];

        const missing: ModuleLibrary[] = [];

        for (const dep of required) {
            // Find the module in library by path
            const depModule = moduleLibrary.find((m: ModuleLibrary) =>
                dep.module === `infra/${m.provider}/${m.name}` || dep.module.endsWith(`/${m.name}`)
            );

            if (depModule) {
                // Check if already in project
                const alreadyInProject = projectModules.some(pm =>
                    pm.module_library_id === depModule.id
                );

                if (!alreadyInProject) {
                    missing.push(depModule);
                }
            }
        }

        return missing;
    };

    const handleNext = async () => {
        if (step === 'select') {
            if (!selectedModule) {
                notify.error('Please select a module');
                return;
            }
            if (!projectId && !selectedProjectId) {
                notify.error('Please select a project');
                return;
            }

            // Check for missing dependencies
            const missing = checkMissingDependencies();
            if (missing.length > 0) {
                setMissingDependencies(missing);
                setShowDependencyPrompt(true);
                return; // Don't proceed until user decides
            }

            // Initialize variables with defaults when proceeding to configure step
            await initializeDefaultVariables(selectedModule);
            setStep('configure');
        } else if (step === 'configure') {
            setStep('confirm');
        }
    };

    const handleBack = () => {
        if (step === 'configure') {
            setStep('select');
        } else if (step === 'confirm') {
            setStep('configure');
        }
    };

    const handleAddWithDependencies = async () => {
        if (!selectedProjectId) return;

        setIsAdding(true);
        setShowDependencyPrompt(false);

        try {
            // Add dependencies first (in order)
            for (const dep of missingDependencies) {
                await addModuleMutation.mutateAsync({
                    projectId: selectedProjectId,
                    data: {
                        module_library_id: dep.id,
                        path_in_project: `infra/${dep.name}`,
                        variable_overrides: {},
                        deployment_order: dep.deployment_order || 0,
                    },
                });
            }

            // Now proceed to configure the main module
            setMissingDependencies([]);
            if (selectedModule) {
                await initializeDefaultVariables(selectedModule);
            }
            setStep('configure');
        } catch (_error) {
            notify.error('Failed to add dependencies');
        } finally {
            setIsAdding(false);
        }
    };

    const handleSkipDependencies = async () => {
        setShowDependencyPrompt(false);
        setMissingDependencies([]);
        if (selectedModule) {
            await initializeDefaultVariables(selectedModule);
        }
        setStep('configure');
    };

    const handleConfirm = async () => {
        if (!selectedProjectId || !selectedModule) return;

        setIsAdding(true);

        try {
            await addModuleMutation.mutateAsync({
                projectId: selectedProjectId,
                data: {
                    module_library_id: selectedModule.id,
                    path_in_project: pathInProject || `infra/${selectedModule.name}`,
                    variable_overrides: variables,
                    deployment_order: selectedModule.deployment_order || 0,
                },
            });

            onOpenChange(false);

            // Reset state
            setStep('select');
            if (!projectId) setSelectedProjectId(null);
            if (!module) setSelectedModule(null);
            setVariables({});
            setPathInProject('');
            setModuleSearch('');
            setMissingDependencies([]);
        } catch (_error) {
            // Error is handled by the mutation hook
        } finally {
            setIsAdding(false);
        }
    };

    const resetState = useCallback(() => {
        setStep('select');
        if (!projectId) {
            setSelectedProjectId(null);
        }
        if (!module) {
            setSelectedModule(null);
        }
        setVariables({});
        setPathInProject('');
        setModuleSearch('');
        setMissingDependencies([]);
        setShowDependencyPrompt(false);
    }, [module, projectId]);

    const handleClose = () => {
        onOpenChange(false);
        setTimeout(() => {
            resetState();
        }, 300);
    };

    useEffect(() => {
        if (step !== 'configure' || !selectedModule || !selectedProject) {
            return;
        }

        setVariables((prev) => {
            let changed = false;
            const next = { ...prev };

            for (const variable of selectedModule.variables || []) {
                const inheritedValue = getProjectInheritedVariable(selectedProject, variable.name);
                if (inheritedValue !== undefined && next[variable.name] === undefined) {
                    next[variable.name] = inheritedValue;
                    changed = true;
                }
            }

            return changed ? next : prev;
        });
    }, [getProjectInheritedVariable, selectedModule, selectedProject, step]);

    return (
        <Dialog open={open} onOpenChange={handleClose}>
            <DialogContent className="sm:max-w-[760px] h-[85vh] overflow-hidden flex flex-col p-0 gap-0">
                <DialogHeader>
                    <div className="px-6 pt-6">
                    <DialogTitle className="flex items-center gap-2">
                        <Package className="h-5 w-5" />
                        Add Module to Project
                    </DialogTitle>
                    <DialogDescription>
                        {selectedModule ? `Configure and add ${selectedModule.name} to your project` : 'Select a module from the library'}
                    </DialogDescription>
                    </div>
                </DialogHeader>

                {step === 'select' && !module && (
                    <div className="px-6 pt-4 space-y-3">
                        <div className="flex flex-wrap gap-2">
                            <Button
                                size="sm"
                                variant="outline"
                                onClick={() => setShowRegistryBrowse(true)}
                            >
                                <Globe className="h-4 w-4 mr-2" />
                                Browse Public Registry
                            </Button>
                            <Button
                                size="sm"
                                variant="outline"
                                onClick={() => setShowGitImport(true)}
                            >
                                <FolderGit2 className="h-4 w-4 mr-2" />
                                Add from Git URL
                            </Button>
                            <Button
                                size="sm"
                                variant="outline"
                                onClick={() => setShowTFCImport(true)}
                            >
                                <Cloud className="h-4 w-4 mr-2" />
                                Add from Terraform Cloud
                            </Button>
                        </div>
                        <div>
                            <Label>Select Module from Library</Label>
                            <div className="relative mt-2">
                                <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
                                <Input
                                    placeholder="Search modules..."
                                    aria-label="Search modules"
                                    value={moduleSearch}
                                    onChange={(e) => setModuleSearch(e.target.value)}
                                    className="pl-9"
                                />
                            </div>
                        </div>
                        <div className="max-h-[200px] overflow-y-auto space-y-2 border rounded-md p-2">
                            {filteredModules.length === 0 ? (
                                <p className="text-sm text-muted-foreground text-center py-4">No modules found</p>
                            ) : (
                                filteredModules.map((m: ModuleLibrary) => (
                                    <button
                                        key={m.id}
                                        onClick={() => setSelectedModule(m)}
                                        className={`w-full text-left p-3 rounded-md border transition-colors ${
                                            selectedModule?.id === m.id
                                                ? 'border-primary bg-primary/5'
                                                : 'border-border hover:border-primary/50'
                                        }`}
                                    >
                                        <div className="font-medium text-sm">{m.name}</div>
                                        <div className="text-xs text-muted-foreground mt-1">{m.description}</div>
                                        <div className="flex gap-1 mt-2">
                                            <Badge variant="secondary" className="text-xs">{m.category}</Badge>
                                            {m.provider && <Badge variant="outline" className="text-xs">{m.provider}</Badge>}
                                        </div>
                                    </button>
                                ))
                            )}
                        </div>
                        <Separator />
                    </div>
                )}

                {/* Progress Indicator */}
                <div className="px-6 py-4 border-t border-b bg-muted/20 flex items-center justify-center gap-2">
                    <div className={`flex items-center gap-2 ${step === 'select' ? 'text-primary' : 'text-muted-foreground'}`}>
                        <div className={`w-8 h-8 rounded-full flex items-center justify-center border-2 ${step !== 'select' ? 'bg-primary text-primary-foreground border-primary' : 'border-current'
                            }`}>
                            {step !== 'select' ? <CheckCircle2 className="h-4 w-4" /> : '1'}
                        </div>
                        <span className="text-sm font-medium">Select Project</span>
                    </div>

                    <div className="w-12 h-0.5 bg-border" />

                    <div className={`flex items-center gap-2 ${step === 'configure' ? 'text-primary' : 'text-muted-foreground'}`}>
                        <div className={`w-8 h-8 rounded-full flex items-center justify-center border-2 ${step === 'confirm' ? 'bg-primary text-primary-foreground border-primary' : 'border-current'
                            }`}>
                            {step === 'confirm' ? <CheckCircle2 className="h-4 w-4" /> : '2'}
                        </div>
                        <span className="text-sm font-medium">Configure</span>
                    </div>

                    <div className="w-12 h-0.5 bg-border" />

                    <div className={`flex items-center gap-2 ${step === 'confirm' ? 'text-primary' : 'text-muted-foreground'}`}>
                        <div className="w-8 h-8 rounded-full flex items-center justify-center border-2 border-current">
                            3
                        </div>
                        <span className="text-sm font-medium">Confirm</span>
                    </div>
                </div>

                {/* Step Content */}
                <div className="px-6 py-5 flex-1 overflow-y-auto">
                    {step === 'select' && (
                        <div className="space-y-6 h-full">
                            <div>
                                <Label>Select Project</Label>
                                <p className="text-sm text-muted-foreground mt-1 mb-3">
                                    Choose which project to add this module to
                                </p>

                                <DropdownMenu>
                                    <DropdownMenuTrigger asChild>
                                        <Button variant="outline" className="w-full justify-between">
                                            {selectedProject ? (
                                                <span className="flex items-center gap-2">
                                                    <FolderGit2 className="h-4 w-4" />
                                                    {selectedProject.name}
                                                </span>
                                            ) : (
                                                <span className="text-muted-foreground">Select a project...</span>
                                            )}
                                            <ChevronDown className="h-4 w-4" />
                                        </Button>
                                    </DropdownMenuTrigger>
                                    <DropdownMenuContent className="w-[--radix-dropdown-menu-trigger-width] min-w-[400px]" align="start">
                                        {projects?.map((project) => (
                                            <DropdownMenuItem
                                                key={project.id}
                                                onClick={() => setSelectedProjectId(project.id)}
                                            >
                                                <FolderGit2 className="h-4 w-4 mr-2" />
                                                {project.name}
                                            </DropdownMenuItem>
                                        ))}
                                    </DropdownMenuContent>
                                </DropdownMenu>
                            </div>

                            {selectedProject && (
                                <div className="p-4 bg-muted rounded-md">
                                    <div className="flex items-start justify-between">
                                        <div>
                                            <p className="font-medium">{selectedProject.name}</p>
                                            <p className="text-sm text-muted-foreground mt-1">
                                                {selectedProject.description || 'No description'}
                                            </p>
                                        </div>
                                    </div>
                                </div>
                            )}
                        </div>
                    )}

                    {step === 'configure' && (
                        <div className="space-y-6 h-full">
                            <div>
                                <h4 className="font-medium mb-2">Configure Module</h4>
                                <p className="text-sm text-muted-foreground">
                                    Review your selection, set the logical path, and edit module variables on this dedicated step.
                                </p>
                            </div>

                            <div className="grid gap-4 lg:grid-cols-[240px_minmax(0,1fr)]">
                                <div className="space-y-4">
                                    {selectedModule && (
                                        <div className="p-4 bg-muted rounded-md">
                                            <div className="flex items-center gap-2 mb-2">
                                                <Package className="h-4 w-4" />
                                                <span className="font-medium">Module</span>
                                            </div>
                                            <p className="text-sm font-medium">{selectedModule.name}</p>
                                            <p className="text-xs text-muted-foreground mt-1">{selectedModule.description}</p>
                                            <div className="flex gap-2 mt-3">
                                                <Badge variant="secondary" className="text-xs">{selectedModule.category}</Badge>
                                                <Badge variant="outline" className="text-xs">{selectedModule.provider}</Badge>
                                            </div>
                                        </div>
                                    )}

                                    {selectedProject && (
                                        <div className="p-4 bg-muted rounded-md">
                                            <div className="flex items-center gap-2 mb-2">
                                                <FolderGit2 className="h-4 w-4" />
                                                <span className="font-medium">Project</span>
                                            </div>
                                            <p className="text-sm font-medium">{selectedProject.name}</p>
                                            <p className="text-xs text-muted-foreground mt-1">{selectedProject.region || 'No region set'}</p>
                                        </div>
                                    )}
                                </div>

                                <div className="space-y-4 min-w-0">
                                    <div className="space-y-2">
                                        <Label htmlFor="path">Logical Path (Optional)</Label>
                                        <Input
                                            id="path"
                                            placeholder={selectedModule ? `infra/${selectedModule.name}` : 'infra/module-name'}
                                            value={pathInProject}
                                            onChange={(e) => setPathInProject(e.target.value)}
                                        />
                                        <p className="text-xs text-muted-foreground">
                                            A logical identifier for this module in your project. Leave blank to use the default path.
                                        </p>
                                    </div>

                                    <div>
                                        <h4 className="font-medium mb-2">Variables</h4>
                                        <ModuleVariableEditor
                                            variables={selectedModule?.variables || []}
                                            values={variables}
                                            onChange={setVariables}
                                        />
                                    </div>
                                </div>
                            </div>
                        </div>
                    )}

                    {step === 'confirm' && (
                        <div className="space-y-6 h-full">
                            <div>
                                <h4 className="font-medium mb-2">Review Configuration</h4>
                                <p className="text-sm text-muted-foreground">
                                    Please review before adding the module
                                </p>
                            </div>

                            <div className="space-y-3">
                                {selectedModule && (
                                    <div className="p-4 bg-muted rounded-md">
                                        <div className="flex items-center gap-2 mb-2">
                                            <Package className="h-4 w-4" />
                                            <span className="font-medium">Module</span>
                                        </div>
                                        <p className="text-sm">{selectedModule.name} v{selectedModule.version}</p>
                                        <div className="flex gap-2 mt-2">
                                            <Badge variant="secondary" className="text-xs">{selectedModule.category}</Badge>
                                            <Badge variant="outline" className="text-xs">{selectedModule.provider}</Badge>
                                        </div>
                                    </div>
                                )}

                                <div className="p-4 bg-muted rounded-md">
                                    <div className="flex items-center gap-2 mb-2">
                                        <FolderGit2 className="h-4 w-4" />
                                        <span className="font-medium">Project</span>
                                    </div>
                                    <p className="text-sm">{selectedProject?.name}</p>
                                </div>

                                {Object.keys(variables).length > 0 && (
                                    <div className="p-4 bg-muted rounded-md">
                                        <div className="font-medium mb-2">Variables</div>
                                        <div className="space-y-2">
                                            {Object.entries(variables).map(([key, value]) => (
                                                <div key={key} className="flex justify-between text-sm">
                                                    <code className="text-muted-foreground">{key}:</code>
                                                    <code className="font-mono">{formatReviewValue(key, value)}</code>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                )}
                            </div>
                        </div>
                    )}
                </div>

                {/* Dependency Prompt */}
                {showDependencyPrompt && missingDependencies.length > 0 && (
                    <Alert className="bg-warning/10 border-warning/20">
                        <AlertCircle className="h-4 w-4 text-warning" />
                        <AlertDescription className="space-y-3">
                            <div className="text-sm font-medium text-warning">
                                This module requires {missingDependencies.length} {missingDependencies.length === 1 ? 'dependency' : 'dependencies'}:
                            </div>
                            <div className="space-y-2">
                                {missingDependencies.map((dep) => (
                                    <div key={dep.id} className="flex items-center gap-2 p-2 bg-background rounded border border-warning/20">
                                        <Package className="h-4 w-4 text-warning flex-shrink-0" />
                                        <div className="flex-1">
                                            <div className="text-sm font-medium">{dep.name}</div>
                                            <div className="text-xs text-muted-foreground">{dep.description}</div>
                                        </div>
                                        <Badge variant="secondary" className="text-xs">
                                            {dep.provider}
                                        </Badge>
                                    </div>
                                ))}
                            </div>
                            <div className="flex gap-2 pt-2">
                                <Button
                                    onClick={handleAddWithDependencies}
                                    disabled={isAdding}
                                    className="flex-1"
                                >
                                    {isAdding ? (
                                        <>
                                            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                            Adding dependencies...
                                        </>
                                    ) : (
                                        <>
                                            <CheckCircle2 className="h-4 w-4 mr-2" />
                                            Add Dependencies & Continue
                                        </>
                                    )}
                                </Button>
                                <Button
                                    variant="outline"
                                    onClick={handleSkipDependencies}
                                    disabled={isAdding}
                                >
                                    Skip for Now
                                </Button>
                            </div>
                        </AlertDescription>
                    </Alert>
                )}

                <DialogFooter className="px-6 py-4 border-t bg-background">
                    {step !== 'select' && !showDependencyPrompt && (
                        <Button variant="outline" onClick={handleBack} disabled={isAdding}>
                            Back
                        </Button>
                    )}

                    {step !== 'confirm' && !showDependencyPrompt ? (
                        <Button onClick={handleNext}>
                            Next
                        </Button>
                    ) : !showDependencyPrompt ? (
                        <Button onClick={handleConfirm} disabled={isAdding}>
                            {isAdding ? (
                                <>
                                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                    Adding...
                                </>
                            ) : (
                                <>
                                    <CheckCircle2 className="h-4 w-4 mr-2" />
                                    Add Module
                                </>
                            )}
                        </Button>
                    ) : null}
                </DialogFooter>
            </DialogContent>
            <RegistryBrowseDialog
                open={showRegistryBrowse}
                onOpenChange={setShowRegistryBrowse}
                onImported={(m) => handleRegistryImported({ id: m.id } as ModuleLibrary)}
            />
            <PublicGitImportDialog
                open={showGitImport}
                onOpenChange={setShowGitImport}
                onImported={handleRegistryImported}
            />
            <TFCImportDialog
                open={showTFCImport}
                onOpenChange={setShowTFCImport}
                onImported={handleRegistryImported}
            />
        </Dialog>
    );
}
