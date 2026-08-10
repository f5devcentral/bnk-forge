import { SectionCard } from '@/components/ui/section-card';
import { Badge } from '@/components/ui/badge';
import { useSystemHealth } from '@/hooks/useSystem';
import { Activity, Database, HardDrive, Server } from 'lucide-react';
import type { ServiceStatus } from '@/types/system';

interface ServiceItemProps {
  name: string;
  status: ServiceStatus;
  icon: React.ReactNode;
}

function ServiceItem({ name, status, icon }: ServiceItemProps) {
  const getStatusColor = (s: ServiceStatus['status']) => {
    switch (s) {
      case 'healthy':
        return 'bg-success';
      case 'degraded':
        return 'bg-warning';
      case 'offline':
        return 'bg-destructive';
      default:
        return 'bg-muted-foreground';
    }
  };

  const getStatusBadge = (s: ServiceStatus['status']) => {
    switch (s) {
      case 'healthy':
        return <Badge variant="success">Online</Badge>;
      case 'degraded':
        return <Badge variant="warning">Degraded</Badge>;
      case 'offline':
        return <Badge variant="destructive">Offline</Badge>;
      default:
        return <Badge variant="secondary">Unknown</Badge>;
    }
  };

  return (
    <div className="flex items-center justify-between py-3 border-b border-border last:border-0">
      <div className="flex items-center gap-3">
        <div className="text-muted-foreground">{icon}</div>
        <div>
          <p className="font-medium">{name}</p>
          {status.error && (
            <p className="text-sm text-destructive">{status.error}</p>
          )}
        </div>
      </div>
      <div className="flex items-center gap-3">
        {status.response_time_ms != null && (
          <span className="text-sm text-muted-foreground">
            {status.response_time_ms.toFixed(1)}ms
          </span>
        )}
        {status.workers !== undefined && (
          <span className="text-sm text-muted-foreground">
            {status.workers} {status.workers === 1 ? 'worker' : 'workers'}
          </span>
        )}
        {getStatusBadge(status.status)}
        <div className={`w-2 h-2 rounded-full ${getStatusColor(status.status)}`} />
      </div>
    </div>
  );
}

export function SystemHealthPanel() {
  const { data, isLoading, error } = useSystemHealth();

  if (isLoading) {
    return (
      <SectionCard title="Service Health">
        <p className="text-sm text-muted-foreground">Loading system status...</p>
      </SectionCard>
    );
  }

  if (error) {
    return (
      <SectionCard title="Service Health">
        <p className="text-sm text-destructive">Failed to load system health</p>
      </SectionCard>
    );
  }

  if (!data) return null;

  return (
    <SectionCard title="Service Health">
      <p className="text-sm text-muted-foreground mb-4">
        Real-time system status (auto-refreshes every 10 seconds)
      </p>
      <div className="space-y-1">
        <ServiceItem
          name="Backend API"
          status={data.services.backend}
          icon={<Server className="h-4 w-4" />}
        />
        <ServiceItem
          name="PostgreSQL"
          status={data.services.database}
          icon={<Database className="h-4 w-4" />}
        />
        <ServiceItem
          name="Redis"
          status={data.services.redis}
          icon={<HardDrive className="h-4 w-4" />}
        />
        <ServiceItem
          name="Celery Workers"
          status={data.services.celery}
          icon={<Activity className="h-4 w-4" />}
        />
      </div>
    </SectionCard>
  );
}
