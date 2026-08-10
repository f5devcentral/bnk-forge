import { SectionCard } from '@/components/ui/section-card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  RefreshCw,
  Activity,
  AlertTriangle,
  CheckCircle,
  XCircle,
  Server,
  Database,
  Zap,
  TrendingUp,
  AlertCircle,
} from 'lucide-react';
import {
  useSystemHealth,
  useQueueMetrics,
  usePerformanceMetrics,
  useRecentErrors,
} from '@/hooks/useSystem';
import { DISPLAY_LIMITS } from '@/lib/constants';
import type { ServiceStatus, TaskError } from '@/types';

type StatusTone = 'success' | 'warning' | 'destructive' | 'muted';

type SystemStatus = {
  status: 'critical' | 'degraded' | 'healthy' | 'unknown';
  tone: StatusTone;
  icon?: typeof CheckCircle;
};

// Map a status tone to its token-pure foreground class.
const TONE_TEXT: Record<StatusTone, string> = {
  success: 'text-success',
  warning: 'text-warning',
  destructive: 'text-destructive',
  muted: 'text-muted-foreground',
};

type BadgeVariant = 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning' | 'info' | 'muted';

const SERVICE_BADGE_VARIANT: Record<string, BadgeVariant> = {
  healthy: 'success',
  degraded: 'warning',
  offline: 'destructive',
};

export default function PerformanceMonitor() {
  const { data: health, isLoading: healthLoading, refetch: refetchHealth } = useSystemHealth();
  const { data: queueMetrics, isLoading: queueLoading } = useQueueMetrics();
  const { data: perfMetrics, isLoading: perfLoading } = usePerformanceMetrics();
  const { data: errors, isLoading: errorsLoading } = useRecentErrors(5);

  const isLoading = healthLoading || queueLoading || perfLoading || errorsLoading;

  // Determine overall system status
  const getSystemStatus = (): SystemStatus => {
    if (!health?.services) return { status: 'unknown', tone: 'muted' };

    const services = Object.values(health.services);
    const allHealthy = services.every((s: { status: string }) => s.status === 'healthy');
    const anyOffline = services.some((s: { status: string }) => s.status === 'offline');
    const anyDegraded = services.some((s: { status: string }) => s.status === 'degraded');

    if (anyOffline) return { status: 'critical', tone: 'destructive', icon: XCircle };
    if (anyDegraded) return { status: 'degraded', tone: 'warning', icon: AlertTriangle };
    if (allHealthy) return { status: 'healthy', tone: 'success', icon: CheckCircle };
    return { status: 'unknown', tone: 'muted', icon: AlertCircle };
  };

  const systemStatus = getSystemStatus();
  const toneText = TONE_TEXT[systemStatus.tone];

  // Check for performance degradation
  const checkDegradation = () => {
    const alerts = [];

    // Check average task duration - alert if tasks are taking unusually long (> 30 minutes)
    // Note: This is task execution time, not API response time (see ADR-019)
    const avgDurationSeconds = perfMetrics?.api?.avg_task_duration_seconds ||
                               (perfMetrics?.api?.avg_response_time_ms ? perfMetrics.api.avg_response_time_ms / 1000 : 0);
    if (avgDurationSeconds > 1800) { // 30 minutes
      alerts.push({
        severity: 'medium',
        message: `Long average task duration: ${(avgDurationSeconds / 60).toFixed(1)} minutes`,
      });
    }

    // Check task queue depth
    if (queueMetrics?.tasks?.pending && queueMetrics.tasks.pending > 50) {
      alerts.push({
        severity: 'medium',
        message: `High task queue depth: ${queueMetrics.tasks.pending} pending tasks`,
      });
    }

    // Check worker availability
    if (queueMetrics?.workers?.total === 0) {
      alerts.push({
        severity: 'high',
        message: 'No Celery workers available',
      });
    }

    // Check if worker inspection timed out (indicates workers are very busy)
    if (queueMetrics?.workers?.inspection_timeout) {
      alerts.push({
        severity: 'low',
        message: 'Worker inspection timed out (workers may be busy with long operations)',
      });
    }

    // Check error rate
    const failedTasks = perfMetrics?.api?.failed_tasks_last_hour || perfMetrics?.api?.failed_requests_last_hour || 0;
    const totalTasks = perfMetrics?.api?.tasks_last_hour || perfMetrics?.api?.requests_last_hour || 0;
    if (failedTasks && totalTasks) {
      const errorRate = (failedTasks / totalTasks) * 100;
      if (errorRate > 10) {
        alerts.push({
          severity: 'high',
          message: `High task failure rate: ${errorRate.toFixed(1)}% in last hour`,
        });
      }
    }

    return alerts;
  };

  const degradationAlerts = perfMetrics && queueMetrics ? checkDegradation() : [];

  const getServiceIcon = (serviceName: string) => {
    switch (serviceName) {
      case 'backend':
        return Server;
      case 'database':
        return Database;
      case 'redis':
      case 'celery':
        return Zap;
      default:
        return Activity;
    }
  };

  const failedLastHour = perfMetrics?.api?.failed_tasks_last_hour ?? perfMetrics?.api?.failed_requests_last_hour ?? 0;

  return (
    <div className="space-y-6">
      {/* Overall System Status */}
      <SectionCard>
        <div className="flex flex-row items-start justify-between gap-4 mb-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Performance monitor
            </p>
            <p className="text-sm text-muted-foreground mt-1">
              Real-time monitoring and performance metrics
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => refetchHealth()}
            disabled={isLoading}
          >
            <RefreshCw className={`h-4 w-4 mr-2 ${isLoading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
        </div>
        <div className="space-y-6">
          {/* System Status Banner — neutral surface + tone-coloured icon/label, no whole-card tint */}
          <div className="p-4 rounded-lg border border-border bg-muted/50 flex items-center gap-4">
            {systemStatus.icon && (
              <systemStatus.icon className={`h-8 w-8 ${toneText}`} />
            )}
            <div className="flex-1">
              <div className="text-sm text-muted-foreground">
                Overall Status
              </div>
              <div className={`text-xl font-bold capitalize ${toneText}`}>
                {systemStatus.status}
              </div>
            </div>
            {health?.timestamp && (
              <div className="text-xs text-muted-foreground">
                Updated: {new Date(health.timestamp).toLocaleTimeString()}
              </div>
            )}
          </div>

          {/* Degradation Alerts — destructive accent via 2px left border, not whole-card tint */}
          {degradationAlerts.length > 0 && (
            <div className="rounded-lg border border-border border-l-2 border-l-destructive bg-muted/50 p-4">
              <div className="flex items-center gap-2 mb-2">
                <AlertTriangle className="h-5 w-5 text-destructive" />
                <h3 className="font-semibold text-destructive">
                  Performance Alerts
                </h3>
              </div>
              <ul className="space-y-1">
                {degradationAlerts.map((alert, idx) => (
                  <li
                    key={idx}
                    className="text-sm text-foreground/80"
                  >
                    • {alert.message}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Service Health */}
          <div>
            <h3 className="text-sm font-semibold mb-3 text-foreground">
              Service Health
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {health?.services && Object.entries(health.services).map(([serviceName, serviceData]: [string, ServiceStatus]) => {
                const ServiceIcon = getServiceIcon(serviceName);
                const badgeVariant = SERVICE_BADGE_VARIANT[serviceData.status] ?? 'muted';
                return (
                  <div
                    key={serviceName}
                    className="p-3 rounded-lg border border-border bg-card"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <ServiceIcon className="h-5 w-5 text-muted-foreground" />
                        <div>
                          <div className="font-medium capitalize text-foreground">
                            {serviceName}
                          </div>
                          {serviceData.response_time_ms != null && (
                            <div className="text-xs text-muted-foreground">
                              {serviceData.response_time_ms.toFixed(0)}ms
                            </div>
                          )}
                          {serviceData.workers !== undefined && (
                            <div className="text-xs text-muted-foreground">
                              {serviceData.workers} worker(s), {serviceData.active_tasks} active
                            </div>
                          )}
                        </div>
                      </div>
                      <Badge variant={badgeVariant} className="capitalize">
                        {serviceData.status}
                      </Badge>
                    </div>
                    {serviceData.error && (
                      <div className="text-xs mt-2 text-destructive">
                        {serviceData.error}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          {/* Task Queue Metrics */}
          {queueMetrics && (
            <div>
              <h3 className="text-sm font-semibold mb-3 text-foreground">
                Task Queue
              </h3>
              <div className="grid grid-cols-3 gap-4">
                <div className="p-4 rounded-lg border border-border bg-muted/50">
                  <div className="text-sm mb-1 text-muted-foreground">
                    Pending
                  </div>
                  <div className="text-2xl font-bold text-foreground">
                    {queueMetrics.tasks?.pending || 0}
                  </div>
                </div>
                <div className="p-4 rounded-lg border border-border bg-muted/50">
                  <div className="text-sm mb-1 text-muted-foreground">
                    Active
                  </div>
                  <div className="text-2xl font-bold text-foreground">
                    {queueMetrics.tasks?.active || 0}
                  </div>
                </div>
                <div className="p-4 rounded-lg border border-border bg-muted/50">
                  <div className="text-sm mb-1 text-muted-foreground">
                    Completed (1h)
                  </div>
                  <div className="text-2xl font-bold text-foreground">
                    {queueMetrics.tasks?.completed_last_hour || 0}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Performance Metrics */}
          {perfMetrics && (
            <div>
              <h3 className="text-sm font-semibold mb-3 text-foreground">
                Performance Metrics
              </h3>
              <div className="space-y-3">
                {/* API Metrics */}
                {/* Task Performance - Note: This shows OpenTofu task duration, not API response time */}
                <div className="p-4 rounded-lg border border-border bg-card">
                  <div className="flex items-center gap-3 mb-2">
                    <TrendingUp className="h-5 w-5 text-muted-foreground" />
                    <div className="font-medium text-foreground">
                      Task Performance
                    </div>
                  </div>
                  <div className="grid grid-cols-3 gap-4">
                    <div>
                      <div className="text-xs text-muted-foreground">
                        Avg Task Duration
                      </div>
                      <div className="text-lg font-semibold text-foreground">
                        {/* Show in seconds if > 60s, otherwise ms */}
                        {(() => {
                          const avgDurationSeconds =
                            perfMetrics.api?.avg_task_duration_seconds
                            ?? (perfMetrics.api?.avg_response_time_ms != null ? perfMetrics.api.avg_response_time_ms / 1000 : 0);
                          return avgDurationSeconds > 60
                            ? `${(avgDurationSeconds / 60).toFixed(1)}m`
                            : `${avgDurationSeconds.toFixed(0)}s`;
                        })()}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-muted-foreground">
                        Tasks (1h)
                      </div>
                      <div className="text-lg font-semibold text-foreground">
                        {perfMetrics.api?.tasks_last_hour || perfMetrics.api?.requests_last_hour || 0}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-muted-foreground">
                        Failed (1h)
                      </div>
                      <div className={`text-lg font-semibold ${failedLastHour > 0 ? 'text-destructive' : 'text-foreground'}`}>
                        {failedLastHour}
                      </div>
                    </div>
                  </div>
                </div>

                {/* Database Metrics */}
                {perfMetrics.database && (
                  <div className="p-4 rounded-lg border border-border bg-card">
                    <div className="flex items-center gap-3 mb-2">
                      <Database className="h-5 w-5 text-muted-foreground" />
                      <div className="font-medium text-foreground">
                        Database Performance
                      </div>
                    </div>
                    <div className="grid grid-cols-3 gap-4">
                      <div>
                        <div className="text-xs text-muted-foreground">
                          Size
                        </div>
                        <div className="text-lg font-semibold text-foreground">
                          {perfMetrics.database.size_mb != null ? `${perfMetrics.database.size_mb.toFixed(2)}MB` : '0MB'}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-muted-foreground">
                          Connections
                        </div>
                        <div className="text-lg font-semibold text-foreground">
                          {perfMetrics.database.connections || 0}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-muted-foreground">
                          Slow Queries
                        </div>
                        <div className="text-lg font-semibold text-foreground">
                          {perfMetrics.database.slow_queries_last_hour || 0}
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Recent Errors */}
          {errors && errors.errors.length > 0 && (
            <div>
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold text-foreground">
                  Recent Errors
                </h3>
                <Badge variant="outline">
                  {errors.total} total
                </Badge>
              </div>
              <div className="space-y-2">
                {errors.errors.slice(0, DISPLAY_LIMITS.RECENT_ERRORS).map((error: TaskError) => (
                  <div
                    key={error.task_id}
                    className="p-3 rounded-lg border border-border border-l-2 border-l-destructive bg-card"
                  >
                    <div className="flex items-start gap-3">
                      <AlertCircle className="h-4 w-4 mt-0.5 text-destructive" />
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium text-foreground">
                          {error.type.replace(/_/g, ' ')} - {error.project}
                        </div>
                        <div className="text-xs mt-1 text-muted-foreground">
                          {error.error}
                        </div>
                        <div className="text-xs mt-1 text-muted-foreground">
                          {new Date(error.timestamp).toLocaleString()}
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </SectionCard>
    </div>
  );
}
