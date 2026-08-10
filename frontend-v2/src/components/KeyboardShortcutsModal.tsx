import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import {
    getAvailableShortcuts,
    getSequenceShortcuts,
    formatShortcut,
} from '@/hooks/useKeyboardShortcuts';
import { Keyboard } from 'lucide-react';

interface KeyboardShortcutsModalProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
}

export function KeyboardShortcutsModal({ open, onOpenChange }: KeyboardShortcutsModalProps) {
    const shortcuts = getAvailableShortcuts();
    const sequences = getSequenceShortcuts();

    // Group shortcuts by category
    const categories = {
        'General': shortcuts.filter(s => ['k', '/', '?'].includes(s.key)),
        'Navigation': shortcuts.filter(s => ['d', 'p', 'm', 'e'].includes(s.key)),
        'Actions': shortcuts.filter(s => ['n'].includes(s.key)),
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-[600px]">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <Keyboard className="h-5 w-5" />
                        Keyboard Shortcuts
                    </DialogTitle>
                    <DialogDescription>
                        Use these shortcuts to navigate and perform actions quickly
                    </DialogDescription>
                </DialogHeader>

                <div className="space-y-6 py-4">
                    {Object.entries(categories).map(([category, categoryShortcuts]) => (
                        <div key={category}>
                            <h3 className="font-semibold mb-3">{category}</h3>
                            <div className="space-y-2">
                                {categoryShortcuts.map((shortcut, index) => (
                                    <div
                                        key={index}
                                        className="flex items-center justify-between p-2 rounded-md hover:bg-muted/50"
                                    >
                                        <span className="text-sm">{shortcut.description}</span>
                                        <Badge variant="outline" className="font-mono">
                                            {formatShortcut(shortcut)}
                                        </Badge>
                                    </div>
                                ))}
                            </div>
                            <Separator className="mt-4" />
                        </div>
                    ))}

                    {/* G-sequence shortcuts */}
                    <div>
                        <h3 className="font-semibold mb-3">Quick Navigation (G → key)</h3>
                        <p className="text-xs text-muted-foreground mb-3">
                            Press G then a letter within 1 second
                        </p>
                        <div className="grid grid-cols-2 gap-2">
                            {sequences.map((seq, index) => (
                                <div
                                    key={index}
                                    className="flex items-center justify-between p-2 rounded-md hover:bg-muted/50"
                                >
                                    <span className="text-sm">{seq.description}</span>
                                    <Badge variant="outline" className="font-mono text-xs">
                                        {seq.keys}
                                    </Badge>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>

                <div className="text-sm text-muted-foreground text-center">
                    Press <Badge variant="outline" className="font-mono mx-1">⌘/</Badge> or{' '}
                    <Badge variant="outline" className="font-mono mx-1">⇧?</Badge> to toggle this dialog
                </div>
            </DialogContent>
        </Dialog>
    );
}
