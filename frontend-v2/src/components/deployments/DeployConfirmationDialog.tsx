import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { AlertTriangle, Rocket, Clock } from 'lucide-react';

interface DeployConfirmationDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    moduleName: string;
    modulePath: string;
    onConfirm: () => void;
    isDeploying?: boolean;
}

export function DeployConfirmationDialog({
    open,
    onOpenChange,
    moduleName,
    modulePath,
    onConfirm,
    isDeploying = false,
}: DeployConfirmationDialogProps) {
    const handleConfirm = () => {
        onConfirm();
        onOpenChange(false);
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-[500px]">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <Rocket className="h-5 w-5 text-info" />
                        Confirm Deployment
                    </DialogTitle>
                    <DialogDescription>
                        You are about to deploy infrastructure changes. Please review before proceeding.
                    </DialogDescription>
                </DialogHeader>

                <div className="space-y-4 py-4">
                    {/* Module info */}
                    <div className="space-y-2">
                        <div className="flex items-center justify-between">
                            <span className="text-sm font-medium">Module:</span>
                            <Badge variant="secondary">{moduleName}</Badge>
                        </div>
                        <div className="flex items-center justify-between">
                            <span className="text-sm font-medium">Path:</span>
                            <code className="text-xs bg-muted px-2 py-1 rounded">{modulePath}</code>
                        </div>
                    </div>

                    {/* Warning box */}
                    <div className="bg-warning/10 border border-warning/20 rounded-md p-4">
                        <div className="flex gap-3">
                            <AlertTriangle className="h-5 w-5 text-warning flex-shrink-0 mt-0.5" />
                            <div className="space-y-2">
                                <p className="text-sm font-medium text-warning">
                                    This will apply infrastructure changes
                                </p>
                                <ul className="text-sm text-warning space-y-1 list-disc list-inside">
                                    <li>Resources may be created, modified, or destroyed</li>
                                    <li>Changes will be applied to your cloud provider</li>
                                    <li>This action cannot be easily undone</li>
                                </ul>
                            </div>
                        </div>
                    </div>

                    {/* Estimated time */}
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                        <Clock className="h-4 w-4" />
                        <span>Estimated deployment time: 2-5 minutes</span>
                    </div>
                </div>

                <DialogFooter>
                    <Button
                        type="button"
                        variant="outline"
                        onClick={() => onOpenChange(false)}
                        disabled={isDeploying}
                    >
                        Cancel
                    </Button>
                    <Button
                        type="button"
                        onClick={handleConfirm}
                        disabled={isDeploying}
                    >
                        {isDeploying ? (
                            <>
                                <Clock className="h-4 w-4 mr-2 animate-spin" />
                                Deploying...
                            </>
                        ) : (
                            <>
                                <Rocket className="h-4 w-4 mr-2" />
                                Deploy Now
                            </>
                        )}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
