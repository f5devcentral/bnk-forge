import { useState, useEffect, useRef, useCallback } from 'react';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
    Download,
    Copy,
    Filter,
    RefreshCw,
    Terminal,
    Loader2,
    AlertCircle,
    FileText,
} from 'lucide-react';
import { notify } from '@/lib/notify';
import { api } from '@/lib/api';
import { ModuleReportsTab } from '@/components/modules/ModuleReportsTab';
import { cn } from '@/lib/utils';
import type { Task } from '@/types';

/** Map task_type to a display label, using the command field to detect SSH engine tasks. */
function taskDisplayLabel(task: { task_type?: string; command?: string }): string {
    const isSsh = task.command?.startsWith('ssh-engine');
    const t = task.task_type || 'unknown';
    if (isSsh) {
        if (t === 'apply') return 'Run';
        if (t === 'init') return 'Init';
        if (t === 'destroy') return 'Reset';
        return t;
    }
    return t;
}

interface LogViewerModalProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    moduleName: string;
    moduleId: number;
}

type TaskType = 'all' | 'init' | 'plan' | 'apply' | 'destroy';

export function LogViewerModal({ open, onOpenChange, moduleName, moduleId }: LogViewerModalProps) {
    const [view, setView] = useState<'logs' | 'reports'>('logs');
    const [tasks, setTasks] = useState<Task[]>([]);
    const [selectedTask, setSelectedTask] = useState<Task | null>(null);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [filterType, setFilterType] = useState<TaskType>('all');
    const [searchTerm, setSearchTerm] = useState('');
    const [autoScroll, setAutoScroll] = useState(true);
    const logsEndRef = useRef<HTMLDivElement>(null);

    // Fetch tasks for this module
    const loadTasks = useCallback(async () => {
        setIsLoading(true);
        setError(null);
        try {
            const response = await api.getTasks({ module_id: moduleId });
            setTasks(response.tasks);

            // If we have a selected task, update it with fresh data from the list
            setSelectedTask(prev => {
                if (prev) {
                    const updatedSelectedTask = response.tasks.find(t => t.id === prev.id);
                    if (updatedSelectedTask) {
                        return { ...prev, status: updatedSelectedTask.status };
                    }
                }
                return prev;
            });

            // Select and load the most recent task by default if none selected
            if (response.tasks.length > 0) {
                setSelectedTask(prev => {
                    if (!prev) {
                        // Trigger loading of the first task details
                        loadTaskDetails(response.tasks[0].id);
                    }
                    return prev;
                });
            }
        } catch (err: unknown) {
            const message = err instanceof Error ? err.message : 'Failed to load tasks';
            setError(message);
        } finally {
            setIsLoading(false);
        }
    }, [moduleId]);

    // Load full task details including logs
    const loadTaskDetails = async (taskId: number) => {
        try {
            const taskDetails = await api.getTask(taskId);
            setSelectedTask(taskDetails);
        } catch (_err: unknown) {
            notify.error('Failed to load task details');
        }
    };

    useEffect(() => {
        if (open) {
            loadTasks();
        }
    }, [open, moduleId, loadTasks]);

    // Poll for log updates when task is in_progress or queued
    useEffect(() => {
        if (!selectedTask || (selectedTask.status !== 'in_progress' && selectedTask.status !== 'queued')) {
            return;
        }

        // Poll every 2 seconds for in-progress or queued tasks
        const interval = setInterval(async () => {
            try {
                const updatedTask = await api.getTask(selectedTask.id);
                setSelectedTask(updatedTask);

                // If task completed, reload the tasks list to update sidebar
                if (updatedTask.status !== 'in_progress' && updatedTask.status !== 'queued') {
                    await loadTasks();
                }
            } catch {
                // Polling retry - error is non-actionable
            }
        }, 2000);

        return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- selectedTask?.id and ?.status are intentional decompositions; adding full selectedTask would cause infinite loop since this effect updates it
    }, [selectedTask?.id, selectedTask?.status, loadTasks]);

    // Auto-scroll to bottom when logs change
    useEffect(() => {
        if (autoScroll && logsEndRef.current) {
            logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
        }
    }, [selectedTask, autoScroll]);

    const filteredTasks = tasks.filter((task) => {
        const matchesFilter = filterType === 'all' || task.task_type === filterType;
        const matchesSearch = task.task_type?.toLowerCase().includes(searchTerm.toLowerCase()) ||
                            task.command?.toLowerCase().includes(searchTerm.toLowerCase());
        return matchesFilter && matchesSearch;
    });

    const getStatusColor = (status?: string) => {
        switch (status) {
            case 'completed':
                return 'bg-success/10 text-success border-success/20';
            case 'failed':
                return 'bg-destructive/10 text-destructive border-destructive/20';
            case 'in_progress':
                return 'bg-info/10 text-info border-info/20';
            default:
                return 'bg-muted text-muted-foreground border-border';
        }
    };

    const handleDownload = () => {
        if (!selectedTask?.logs) {
            notify.error('No logs to download');
            return;
        }
        const blob = new Blob([selectedTask.logs], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${moduleName}-${taskDisplayLabel(selectedTask)}-logs.txt`;
        a.click();
        URL.revokeObjectURL(url);
    };

    const handleCopy = () => {
        if (!selectedTask?.logs) {
            notify.error('No logs to copy');
            return;
        }
        navigator.clipboard.writeText(selectedTask.logs);
    };

    if (isLoading) {
        return (
            <Dialog open={open} onOpenChange={onOpenChange}>
                <DialogContent className="max-w-4xl h-[80vh] flex flex-col">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Terminal className="h-5 w-5" />
                            Task Logs
                        </DialogTitle>
                        <DialogDescription>
                            {moduleName} (Module #{moduleId})
                        </DialogDescription>
                    </DialogHeader>
                    <div className="flex-1 flex items-center justify-center">
                        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                    </div>
                </DialogContent>
            </Dialog>
        );
    }

    if (error) {
        return (
            <Dialog open={open} onOpenChange={onOpenChange}>
                <DialogContent className="max-w-4xl h-[80vh] flex flex-col">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Terminal className="h-5 w-5" />
                            Task Logs
                        </DialogTitle>
                        <DialogDescription>
                            {moduleName} (Module #{moduleId})
                        </DialogDescription>
                    </DialogHeader>
                    <div className="flex-1 flex flex-col items-center justify-center gap-4">
                        <AlertCircle className="h-12 w-12 text-destructive" />
                        <p className="text-sm text-muted-foreground">{error}</p>
                        <Button onClick={loadTasks}>
                            <RefreshCw className="h-4 w-4 mr-2" />
                            Retry
                        </Button>
                    </div>
                </DialogContent>
            </Dialog>
        );
    }

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-6xl h-[85vh] flex flex-col">
                <DialogHeader>
                    <div className="flex items-center justify-between">
                        <div>
                            <DialogTitle className="flex items-center gap-2">
                                <Terminal className="h-5 w-5" />
                                Task Logs
                            </DialogTitle>
                            <DialogDescription className="mt-1">
                                {moduleName} (Module #{moduleId})
                            </DialogDescription>
                        </div>
                        <div className="flex items-center gap-2">
                            {/* Logs / Reports toggle — the report a run produced lives
                                right next to its logs (D-034 #458). */}
                            <div className="flex rounded-md border overflow-hidden">
                                <button
                                    type="button"
                                    onClick={() => setView('logs')}
                                    className={cn(
                                        'flex items-center gap-1 px-2.5 py-1 text-xs transition-colors',
                                        view === 'logs' ? 'bg-primary/10 text-foreground' : 'text-muted-foreground hover:bg-muted'
                                    )}
                                >
                                    <Terminal className="h-3.5 w-3.5" /> Logs
                                </button>
                                <button
                                    type="button"
                                    onClick={() => setView('reports')}
                                    className={cn(
                                        'flex items-center gap-1 px-2.5 py-1 text-xs transition-colors border-l',
                                        view === 'reports' ? 'bg-primary/10 text-foreground' : 'text-muted-foreground hover:bg-muted'
                                    )}
                                >
                                    <FileText className="h-3.5 w-3.5" /> Reports
                                </button>
                            </div>
                            {view === 'logs' && <Badge variant="secondary">{filteredTasks.length} tasks</Badge>}
                            {view === 'logs' && (
                                <Button variant="outline" size="sm" onClick={loadTasks}>
                                    <RefreshCw className="h-4 w-4" />
                                </Button>
                            )}
                        </div>
                    </div>
                </DialogHeader>

                {view === 'reports' ? (
                    <div className="flex-1 min-h-0 overflow-y-auto">
                        <ModuleReportsTab moduleId={moduleId} />
                    </div>
                ) : (
                <div className="flex gap-4 flex-1 min-h-0">
                    {/* Task List Sidebar */}
                    <div className="w-64 border-r flex flex-col gap-2 pr-4">
                        <div className="flex flex-col gap-2 pb-2 border-b">
                            <Input
                                placeholder="Search tasks..."
                                aria-label="Search tasks"
                                value={searchTerm}
                                onChange={(e) => setSearchTerm(e.target.value)}
                                className="h-8"
                            />
                            <DropdownMenu>
                                <DropdownMenuTrigger asChild>
                                    <Button variant="outline" size="sm" className="w-full">
                                        <Filter className="h-4 w-4 mr-2" />
                                        {filterType === 'all' ? 'All Types' : filterType}
                                    </Button>
                                </DropdownMenuTrigger>
                                <DropdownMenuContent align="start" className="w-48">
                                    <DropdownMenuItem onClick={() => setFilterType('all')}>All Types</DropdownMenuItem>
                                    <DropdownMenuItem onClick={() => setFilterType('init')}>Init</DropdownMenuItem>
                                    <DropdownMenuItem onClick={() => setFilterType('plan')}>Plan</DropdownMenuItem>
                                    <DropdownMenuItem onClick={() => setFilterType('apply')}>Apply</DropdownMenuItem>
                                    <DropdownMenuItem onClick={() => setFilterType('destroy')}>Destroy</DropdownMenuItem>
                                </DropdownMenuContent>
                            </DropdownMenu>
                        </div>

                        <div className="flex-1 overflow-y-auto space-y-1">
                            {filteredTasks.length === 0 ? (
                                <div className="text-center text-sm text-muted-foreground py-4">
                                    No tasks found
                                </div>
                            ) : (
                                filteredTasks.map((task) => (
                                    <button
                                        key={task.id}
                                        onClick={() => loadTaskDetails(task.id)}
                                        className={`w-full text-left p-2 rounded border text-sm transition-colors ${
                                            selectedTask?.id === task.id
                                                ? 'bg-primary/10 border-primary'
                                                : 'hover:bg-muted border-transparent'
                                        }`}
                                    >
                                        <div className="flex items-center justify-between mb-1">
                                            <span className="font-medium">{taskDisplayLabel(task)}</span>
                                            <Badge variant="secondary" className={`text-xs ${getStatusColor(task.status)}`}>
                                                {task.status}
                                            </Badge>
                                        </div>
                                        <div className="text-xs text-muted-foreground">
                                            Task #{task.id}
                                        </div>
                                        {task.created_at && (
                                            <div className="text-xs text-muted-foreground mt-1">
                                                {new Date(String(task.created_at).replace(' ', 'T')).toLocaleString()}
                                            </div>
                                        )}
                                    </button>
                                ))
                            )}
                        </div>
                    </div>

                    {/* Log Content */}
                    <div className="flex-1 flex flex-col min-w-0">
                        {selectedTask ? (
                            <>
                                {/* Toolbar */}
                                <div className="flex items-center justify-between pb-2 border-b mb-2">
                                    <div>
                                        <h4 className="font-semibold">
                                            {taskDisplayLabel(selectedTask).toUpperCase()} - Task #{selectedTask.id}
                                        </h4>
                                        <p className="text-xs text-muted-foreground">
                                            {selectedTask.duration_seconds
                                                ? `Duration: ${selectedTask.duration_seconds.toFixed(2)}s`
                                                : 'Running...'}
                                            {selectedTask.exit_code !== null && ` | Exit code: ${selectedTask.exit_code}`}
                                        </p>
                                    </div>
                                    <div className="flex gap-2">
                                        <Button variant="outline" size="sm" onClick={handleCopy} disabled={!selectedTask.logs}>
                                            <Copy className="h-4 w-4 mr-2" />
                                            Copy
                                        </Button>
                                        <Button variant="outline" size="sm" onClick={handleDownload} disabled={!selectedTask.logs}>
                                            <Download className="h-4 w-4 mr-2" />
                                            Download
                                        </Button>
                                    </div>
                                </div>

                                {/* Log output */}
                                <div className="flex-1 overflow-y-auto bg-muted rounded-md p-4 font-mono text-sm text-foreground">
                                    {selectedTask.logs ? (
                                        <pre className="whitespace-pre-wrap break-words">{selectedTask.logs}</pre>
                                    ) : (
                                        <div className="text-center text-muted-foreground py-8">
                                            No logs available for this task
                                        </div>
                                    )}
                                    <div ref={logsEndRef} />
                                </div>

                                {/* Auto-scroll toggle */}
                                <div className="flex items-center justify-between pt-2 border-t mt-2">
                                    <label className="flex items-center gap-2 text-sm cursor-pointer">
                                        <input
                                            type="checkbox"
                                            checked={autoScroll}
                                            onChange={(e) => setAutoScroll(e.target.checked)}
                                            className="rounded"
                                        />
                                        Auto-scroll to bottom
                                    </label>
                                </div>
                            </>
                        ) : (
                            <div className="flex-1 flex items-center justify-center text-muted-foreground">
                                Select a task to view logs
                            </div>
                        )}
                    </div>
                </div>
                )}
            </DialogContent>
        </Dialog>
    );
}
