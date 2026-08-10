import { SectionCard } from '@/components/ui/section-card';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { CheckCircle2, XCircle, Clock, PlayCircle } from 'lucide-react';

interface DeploymentEvent {
    id: number;
    moduleName: string;
    status: 'deployed' | 'failed' | 'deploying' | 'pending';
    timestamp: string;
    duration?: string;
    user?: string;
}

interface DeploymentTimelineProps {
    events?: DeploymentEvent[];
}

export function DeploymentTimeline({ events = [] }: DeploymentTimelineProps) {
    const getStatusIcon = (status: string) => {
        switch (status) {
            case 'deployed':
                return <CheckCircle2 className="h-5 w-5 text-success" />;
            case 'failed':
                return <XCircle className="h-5 w-5 text-destructive" />;
            case 'deploying':
                return <PlayCircle className="h-5 w-5 text-info animate-pulse" />;
            case 'pending':
                return <Clock className="h-5 w-5 text-warning" />;
            default:
                return <Clock className="h-5 w-5 text-muted-foreground" />;
        }
    };

    const getStatusBadgeVariant = (status: string): BadgeProps['variant'] => {
        switch (status) {
            case 'deployed':
                return 'success';
            case 'failed':
                return 'destructive';
            case 'deploying':
                return 'info';
            case 'pending':
                return 'warning';
            default:
                return 'outline';
        }
    };

    const getTimelineColor = (status: string) => {
        switch (status) {
            case 'deployed':
                return 'bg-success';
            case 'failed':
                return 'bg-destructive';
            case 'deploying':
                return 'bg-info';
            default:
                return 'bg-muted-foreground';
        }
    };

    return (
        <SectionCard title="Deployment timeline">
            <p className="text-sm text-muted-foreground -mt-1 mb-4">
                Recent deployment activity across all modules.
            </p>
            <div className="relative">
                {/* Timeline line */}
                <div className="absolute left-[13px] top-0 bottom-0 w-0.5 bg-border" />

                {/* Events */}
                <div className="space-y-6">
                    {events.map((event) => (
                        <div key={event.id} className="relative flex gap-4">
                            {/* Timeline dot */}
                            <div className={`relative z-10 flex-shrink-0 w-7 h-7 rounded-full border-4 border-background ${getTimelineColor(event.status)}`} />

                            {/* Event content */}
                            <div className="flex-1 pb-6">
                                <div className="flex items-start justify-between gap-4">
                                    <div className="flex-1">
                                        <div className="flex items-center gap-2 mb-1">
                                            {getStatusIcon(event.status)}
                                            <h4 className="font-semibold">{event.moduleName}</h4>
                                            <Badge variant={getStatusBadgeVariant(event.status)}>
                                                {event.status}
                                            </Badge>
                                        </div>
                                        <div className="flex items-center gap-4 text-sm text-muted-foreground">
                                            <span>{event.timestamp}</span>
                                            {event.duration && <span>Duration: {event.duration}</span>}
                                            {event.user && <span>by {event.user}</span>}
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            </div>

            {events.length === 0 && (
                <div className="text-center py-8 text-muted-foreground">
                    <Clock className="h-12 w-12 mx-auto mb-2 opacity-50" />
                    <p>No deployment history yet</p>
                </div>
            )}
        </SectionCard>
    );
}
