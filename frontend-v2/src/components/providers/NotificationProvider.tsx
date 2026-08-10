import { useEffect, useRef, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { notificationsApi } from '@/lib/api/notifications';
import { logger } from '@/lib/logger';

interface DeploymentNotification {
  deployment_id: number;
  module_id: number;
  module_name: string;
  action: string;
  status: string;
  timestamp: string;
  duration?: number;
  error_message?: string;
  resource_changes?: {
    add: number;
    change: number;
    destroy: number;
  };
}

/**
 * Global notification provider for deployment events
 * Connects to WebSocket endpoint and sends notifications to the notification bell
 */
export function NotificationProvider({ children }: { children: React.ReactNode }) {
  const wsRef = useRef<WebSocket | null>(null);
  const connectTimeoutRef = useRef<number | null>(null);
  const notifiedDeploymentsRef = useRef<Set<number>>(new Set());
  const queryClient = useQueryClient();

  const createNotification = useCallback(async (
    type: 'success' | 'error' | 'info' | 'warning',
    title: string,
    message: string,
    resourceType?: string,
    resourceId?: number
  ) => {
    try {
      await notificationsApi.createNotification({
        type,
        title,
        message,
        resource_type: resourceType,
        resource_id: resourceId,
      });

      // Invalidate notifications queries to refresh the bell icon
      queryClient.invalidateQueries({ queryKey: ['notifications'] });
    } catch (error) {
      logger.error('Failed to create notification:', error);
    }
  }, [queryClient]);

  const showDeploymentNotification = useCallback(async (notification: DeploymentNotification) => {
    const moduleName = notification.module_name || `Module ${notification.module_id}`;
    const action = notification.action.toUpperCase();

    let title = '';
    let message = '';
    let type: 'success' | 'error' | 'info' | 'warning' = 'info';

    switch (notification.status) {
      case 'success':
        type = 'success';
        title = `${action} Completed Successfully`;
        message = moduleName;
        if (notification.resource_changes) {
          const changes = [];
          if (notification.resource_changes.add > 0) {
            changes.push(`+${notification.resource_changes.add} added`);
          }
          if (notification.resource_changes.change > 0) {
            changes.push(`~${notification.resource_changes.change} changed`);
          }
          if (notification.resource_changes.destroy > 0) {
            changes.push(`-${notification.resource_changes.destroy} destroyed`);
          }
          if (changes.length > 0) {
            message += ` (${changes.join(', ')})`;
          }
        }
        if (notification.duration) {
          message += ` - Duration: ${Math.round(notification.duration)}s`;
        }
        break;

      case 'failed':
        type = 'error';
        title = `${action} Failed`;
        message = moduleName;
        if (notification.error_message) {
          const errorPreview = notification.error_message.substring(0, 150);
          message += `: ${errorPreview}${notification.error_message.length > 150 ? '...' : ''}`;
        }
        break;

      case 'running':
        type = 'info';
        title = `${action} Started`;
        message = moduleName;
        break;

      case 'cancelled':
        type = 'warning';
        title = `${action} Cancelled`;
        message = moduleName;
        break;
    }

    await createNotification(type, title, message, 'deployment', notification.deployment_id);
  }, [createNotification]);

  useEffect(() => {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsHost = `${wsProtocol}//${window.location.host}`;
    const wsUrl = `${wsHost}/api/notifications/ws/deployments`;

    connectTimeoutRef.current = window.setTimeout(() => {
      try {
        const ws = new WebSocket(wsUrl);

        ws.onopen = () => {
          // Connected
        };

        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);

            // Handle ping/pong messages
            if (data.type === 'ping' || data.type === 'pong') {
              return;
            }

            const notification: DeploymentNotification = data;

            // Deduplicate notifications
            if (notifiedDeploymentsRef.current.has(notification.deployment_id)) {
              return;
            }

            notifiedDeploymentsRef.current.add(notification.deployment_id);

            // Keep only last 100 deployment IDs to prevent memory leak
            if (notifiedDeploymentsRef.current.size > 100) {
              const ids = Array.from(notifiedDeploymentsRef.current);
              notifiedDeploymentsRef.current = new Set(ids.slice(-100));
            }

            // Send to notification bell instead of showing toast
            showDeploymentNotification(notification);
          } catch (err) {
            logger.error('Failed to parse notification:', err);
          }
        };

        ws.onerror = (error) => {
          logger.error('Deployment notification WebSocket error:', error);
        };

        ws.onclose = () => {
          // Disconnected
        };

        wsRef.current = ws;
      } catch (err) {
        logger.error('Failed to create notification WebSocket:', err);
      }
    }, 0);

    return () => {
      if (connectTimeoutRef.current) {
        clearTimeout(connectTimeoutRef.current);
        connectTimeoutRef.current = null;
      }
      if (wsRef.current) {
        wsRef.current.onopen = null;
        wsRef.current.onmessage = null;
        wsRef.current.onerror = null;
        wsRef.current.onclose = null;
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [queryClient, showDeploymentNotification]);

  return <>{children}</>;
}
