import { useState } from 'react';
import {
    Sheet,
    SheetContent,
    SheetDescription,
    SheetHeader,
    SheetTitle,
} from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import {
    Package,
    ExternalLink,
    GitBranch,
    ChevronDown,
    ChevronUp,
    ArrowUpCircle,
} from 'lucide-react';
import type { ModuleLibrary } from '@/types';
import { DISPLAY_LIMITS } from '@/lib/constants';
import { useUpgradeRegistryModule } from '@/hooks/useRegistry';
import { isAxiosError } from 'axios';
import { notify } from '@/lib/notify';
import {
    getCapabilitySummary,
    getCompatibilitySummary,
    getSupportSemanticsSummary,
} from '@/lib/module-compatibility';
import {
    getEnginePresentation,
    getLifecycleSummaryBadges,
    resolveModuleEngineType,
} from '@/lib/module-engine';

interface ModuleDetailSheetProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    module: ModuleLibrary | null;
    onAddToProject: (module: ModuleLibrary) => void;
}

export function ModuleDetailSheet({
    open,
    onOpenChange,
    module,
    onAddToProject,
}: ModuleDetailSheetProps) {
    const [showFullDocs, setShowFullDocs] = useState(false);
    const upgradeMutation = useUpgradeRegistryModule();

    if (!module) return null;

    const handleAddToProject = () => {
        onAddToProject(module);
        onOpenChange(false);
    };

    const handleUpgrade = async () => {
        if (!module.update_available) return;

        try {
            await upgradeMutation.mutateAsync({
                moduleId: module.id,
                // Upgrade to latest by default
            });
            notify.success(`Module upgraded to v${module.latest_version}`, undefined, { category: 'deployment' });
        } catch (error: unknown) {
            const message = isAxiosError(error)
                ? error.response?.data?.detail || error.message
                : error instanceof Error ? error.message : 'Unknown error';
            notify.error(`Upgrade failed: ${message}`);
        }
    };

    // Extract documentation summary (first 3 lines or 200 chars)
    const getDocsSummary = (readme: string | null | undefined): string => {
        if (!readme) return '';
        const lines = readme.split('\n').filter(line => line.trim().length > 0);
        const summary = lines.slice(0, DISPLAY_LIMITS.DESCRIPTION_PREVIEW).join('\n');
        return summary.length > 200 ? summary.substring(0, 200) + '...' : summary;
    };

    const docsSummary = module.readme ? getDocsSummary(module.readme) : '';
    const hasLongDocs = module.readme && module.readme.length > 200;
    const compatibilitySummary = getCompatibilitySummary(module);
    const capabilitySummary = getCapabilitySummary(module);
    const supportSemanticsSummary = getSupportSemanticsSummary(module);
    const resolvedEngineType = resolveModuleEngineType(module);
    const enginePresentation = getEnginePresentation(resolvedEngineType, module.module_type);
    const lifecycleSummary = getLifecycleSummaryBadges(module.lifecycle_capabilities);

    // Extract user-friendly name from module path
    const getModuleName = (dep: { name?: string; module: string }): string => {
        // Use provided name if available
        if (dep.name) return dep.name;

        // Extract name from path (e.g., "infra/aws/vpc" -> "vpc")
        const pathParts = dep.module.split('/');
        const moduleName = pathParts[pathParts.length - 1];

        // Capitalize and format (e.g., "vpc" -> "VPC", "eks" -> "EKS")
        return moduleName.toUpperCase();
    };

    return (
        <Sheet open={open} onOpenChange={onOpenChange}>
            <SheetContent className="w-full sm:max-w-2xl overflow-y-auto">
                <SheetHeader>
                    <div className="flex items-start gap-4">
                        <div className="flex-1">
                            <SheetTitle className="text-2xl">{module.name}</SheetTitle>
                            <SheetDescription className="mt-1">
                                Version {module.version} • {module.provider}
                            </SheetDescription>
                        </div>
                    </div>
                </SheetHeader>

                <div className="mt-6 space-y-6">
                    {/* Quick Info */}
                    <div className="flex gap-2 flex-wrap">
                        <Badge variant="secondary">{module.category}</Badge>
                        <Badge variant="outline">{module.provider}</Badge>
                        <Badge variant="outline" className={enginePresentation.className}>
                            Engine: {enginePresentation.label}
                        </Badge>
                        {module.variables && (
                            <Badge variant="outline">
                                {module.variables.length} variable{module.variables.length !== 1 ? 's' : ''}
                            </Badge>
                        )}
                    </div>

                    <div>
                        <h3 className="font-semibold mb-2">Module Engine & Lifecycle</h3>
                        <div className="text-sm text-muted-foreground space-y-2">
                            <p>
                                This module uses the <span className="font-medium text-foreground">{enginePresentation.label}</span> engine.
                            </p>
                            {lifecycleSummary.length > 0 ? (
                                <div className="flex flex-wrap gap-1.5">
                                    {lifecycleSummary.map((capability) => (
                                        <Badge key={capability} variant="outline" className="text-xs">
                                            Supports {capability}
                                        </Badge>
                                    ))}
                                </div>
                            ) : (
                                <p className="text-xs">
                                    Lifecycle capabilities are not explicitly published for this module. Legacy module defaults apply.
                                </p>
                            )}
                        </div>
                    </div>

                    {/* Update Available Banner */}
                    {module.update_available && module.latest_version && (
                        <div className="p-4 bg-info/10 border border-info/20 rounded-lg">
                            <div className="flex items-start justify-between gap-4">
                                <div className="flex items-start gap-3">
                                    <ArrowUpCircle className="h-5 w-5 text-info flex-shrink-0 mt-0.5" />
                                    <div>
                                        <h4 className="font-semibold text-info mb-1">Update Available</h4>
                                        <p className="text-sm text-muted-foreground">
                                            Version {module.latest_version} is available (currently on {module.version})
                                        </p>
                                    </div>
                                </div>
                                <Button
                                    onClick={handleUpgrade}
                                    disabled={upgradeMutation.isPending}
                                    size="sm"
                                    className="flex-shrink-0"
                                >
                                    {upgradeMutation.isPending ? 'Upgrading...' : 'Upgrade'}
                                </Button>
                            </div>
                        </div>
                    )}

                    {/* Description */}
                    {module.description && (
                        <div>
                            <h3 className="font-semibold mb-2">Description</h3>
                            <p className="text-sm text-muted-foreground">{module.description}</p>
                        </div>
                    )}

                    {(compatibilitySummary || capabilitySummary || supportSemanticsSummary) && (
                        <div>
                            <h3 className="font-semibold mb-2">Declared Platform Compatibility</h3>
                            <div className="text-sm text-muted-foreground space-y-1">
                                {compatibilitySummary && <p>{compatibilitySummary}</p>}
                                {capabilitySummary && <p>{capabilitySummary}</p>}
                                {supportSemanticsSummary && <p>{supportSemanticsSummary}</p>}
                                <p className="text-xs">
                                    Compatibility is declaration-based and does not guarantee runtime support on every cluster.
                                </p>
                            </div>
                        </div>
                    )}

                    <Separator />

                    {/* README / Documentation */}
                    {module.readme && (
                        <div>
                            <h3 className="font-semibold mb-3">Documentation</h3>
                            <div className="bg-muted rounded-md p-4 space-y-3">
                                <div className="text-sm text-muted-foreground whitespace-pre-wrap">
                                    {showFullDocs ? module.readme : docsSummary}
                                </div>

                                <div className="flex gap-2">
                                    {hasLongDocs && (
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            onClick={() => setShowFullDocs(!showFullDocs)}
                                            className="w-full"
                                        >
                                            {showFullDocs ? (
                                                <>
                                                    <ChevronUp className="h-4 w-4 mr-2" />
                                                    Show Less
                                                </>
                                            ) : (
                                                <>
                                                    <ChevronDown className="h-4 w-4 mr-2" />
                                                    Show More
                                                </>
                                            )}
                                        </Button>
                                    )}
                                    {module.documentation_url && (
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            className={hasLongDocs ? 'flex-1' : 'w-full'}
                                            asChild
                                        >
                                            <a
                                                href={module.documentation_url}
                                                target="_blank"
                                                rel="noopener noreferrer"
                                            >
                                                <ExternalLink className="h-4 w-4 mr-2" />
                                                View Full Documentation
                                            </a>
                                        </Button>
                                    )}
                                </div>
                            </div>
                        </div>
                    )}

                    <Separator />

                    {/* Variables */}
                    {module.variables && module.variables.length > 0 && (
                        <div>
                            <h3 className="font-semibold mb-3">Required Variables</h3>
                            <div className="space-y-2">
                                {module.variables.slice(0, DISPLAY_LIMITS.MODULE_VARIABLES_PREVIEW).map((variable) => (
                                    <div
                                        key={variable.name}
                                        className="flex items-start justify-between p-3 bg-muted rounded-md"
                                    >
                                        <div className="flex-1">
                                            <code className="text-sm font-mono text-foreground">{variable.name}</code>
                                            {variable.description && (
                                                <p className="text-xs text-muted-foreground mt-1">
                                                    {variable.description}
                                                </p>
                                            )}
                                        </div>
                                        <Badge variant="outline" className="text-xs">
                                            {variable.type || 'string'}
                                        </Badge>
                                    </div>
                                ))}
                                {module.variables.length > 5 && (
                                    <p className="text-sm text-muted-foreground text-center">
                                        +{module.variables.length - 5} more variables
                                    </p>
                                )}
                            </div>
                        </div>
                    )}


                    {/* Dependencies */}
                    <div>
                        <h3 className="font-semibold mb-3">Dependencies</h3>
                        {module.dependencies_metadata?.required && module.dependencies_metadata.required.length > 0 ? (
                            <div className="space-y-2">
                                {module.dependencies_metadata.required.map((dep, idx) => (
                                    <div
                                        key={idx}
                                        className="flex items-start gap-3 p-3 bg-muted rounded-md"
                                    >
                                        <GitBranch className="h-4 w-4 text-muted-foreground flex-shrink-0 mt-0.5" />
                                        <div className="flex-1">
                                            <div className="text-sm font-semibold text-foreground">{getModuleName(dep)}</div>
                                            <code className="text-xs text-muted-foreground">{dep.module}</code>
                                            {dep.reason && (
                                                <p className="text-xs text-muted-foreground mt-1">
                                                    {dep.reason}
                                                </p>
                                            )}
                                        </div>
                                        <Badge variant="destructive" className="text-xs">
                                            Required
                                        </Badge>
                                    </div>
                                ))}
                                {module.dependencies_metadata.optional && module.dependencies_metadata.optional.length > 0 && (
                                    <>
                                        <p className="text-sm font-medium text-muted-foreground mt-3 mb-2">Optional Dependencies</p>
                                        {module.dependencies_metadata.optional.map((dep, idx) => (
                                            <div
                                                key={idx}
                                                className="flex items-start gap-3 p-3 bg-muted rounded-md"
                                            >
                                                <GitBranch className="h-4 w-4 text-muted-foreground flex-shrink-0 mt-0.5" />
                                                <div className="flex-1">
                                                    <div className="text-sm font-semibold text-foreground">{getModuleName(dep)}</div>
                                                    <code className="text-xs text-muted-foreground">{dep.module}</code>
                                                    {dep.reason && (
                                                        <p className="text-xs text-muted-foreground mt-1">
                                                            {dep.reason}
                                                        </p>
                                                    )}
                                                </div>
                                                <Badge variant="secondary" className="text-xs">
                                                    Optional
                                                </Badge>
                                            </div>
                                        ))}
                                    </>
                                )}
                            </div>
                        ) : (
                            <div className="flex items-center gap-2 text-sm text-muted-foreground">
                                <GitBranch className="h-4 w-4" />
                                <span>No external dependencies</span>
                            </div>
                        )}
                    </div>

                    {/* Inputs from Other Modules */}
                    {module.inputs_metadata?.required && module.inputs_metadata.required.some(input => input.source === 'module') && (
                        <>
                            <Separator />
                            <div>
                                <h3 className="font-semibold mb-3">Inputs from Other Modules</h3>
                                <div className="space-y-2">
                                    {module.inputs_metadata.required
                                        .filter(input => input.source === 'module')
                                        .map((input, idx) => (
                                            <div
                                                key={idx}
                                                className="flex items-start justify-between p-3 bg-muted rounded-md"
                                            >
                                                <div className="flex-1">
                                                    <code className="text-sm font-mono text-foreground">{input.name}</code>
                                                    <p className="text-xs text-muted-foreground mt-1">
                                                        From: <code>{input.from_module}</code> → <code>{input.from_output}</code>
                                                    </p>
                                                    {input.description && (
                                                        <p className="text-xs text-muted-foreground mt-1">
                                                            {input.description}
                                                        </p>
                                                    )}
                                                </div>
                                                <Badge variant="outline" className="text-xs">
                                                    {input.type}
                                                </Badge>
                                            </div>
                                        ))}
                                </div>
                            </div>
                        </>
                    )}

                    {/* Outputs */}
                    {module.outputs_metadata && module.outputs_metadata.length > 0 && (
                        <>
                            <Separator />
                            <div>
                                <h3 className="font-semibold mb-3">Outputs</h3>
                                <div className="space-y-2">
                                    {module.outputs_metadata.map((output, idx) => (
                                        <div
                                            key={idx}
                                            className="flex items-start justify-between p-3 bg-muted rounded-md"
                                        >
                                            <div className="flex-1">
                                                <code className="text-sm font-mono text-foreground">{output.name}</code>
                                                {output.description && (
                                                    <p className="text-xs text-muted-foreground mt-1">
                                                        {output.description}
                                                    </p>
                                                )}
                                                {output.used_by && output.used_by.length > 0 && (
                                                    <p className="text-xs text-info mt-1">
                                                        Used by: {output.used_by.join(', ')}
                                                    </p>
                                                )}
                                            </div>
                                            <Badge variant="outline" className="text-xs">
                                                {output.type}
                                            </Badge>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        </>
                    )}

                    {/* Deployment Order */}
                    {module.deployment_order !== undefined && module.deployment_order !== 999 && (
                        <>
                            <Separator />
                            <div>
                                <h3 className="font-semibold mb-3">Deployment Order</h3>
                                <div className="flex items-center gap-2 p-3 bg-muted rounded-md">
                                    <Badge variant="outline" className="text-lg">
                                        {module.deployment_order}
                                    </Badge>
                                    <span className="text-sm text-muted-foreground">
                                        This module will be deployed in order {module.deployment_order}
                                    </span>
                                </div>
                            </div>
                        </>
                    )}

                    {/* Action Buttons */}
                    <div className="sticky bottom-0 bg-background pt-4 pb-2 border-t">
                        <Button onClick={handleAddToProject} className="w-full" size="lg">
                            <Package className="h-4 w-4 mr-2" />
                            Add to Project
                        </Button>
                    </div>
                </div>
            </SheetContent>
        </Sheet>
    );
}
