import { useTaskWebSocket } from '@/hooks/useTaskWebSocket';
import { Badge } from '@/components/ui/badge';

/**
 * WebSocket Provider Component
 *
 * Establishes and maintains WebSocket connection for real-time task updates.
 * Shows reconnection status to user when connection is lost.
 */
export function WebSocketProvider({ children }: { children: React.ReactNode }) {
  const { isConnected, gaveUp } = useTaskWebSocket();

  return (
    <>
      {children}

      {/* Show reconnecting indicator only while actively retrying */}
      {!isConnected && !gaveUp && (
        <div className="fixed bottom-4 right-4 z-50">
          <Badge variant="outline" className="bg-warning/10 border-warning/30 text-warning px-3 py-1.5 shadow-lg">
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 bg-warning rounded-full animate-pulse" />
              <span className="text-xs font-medium">Reconnecting...</span>
            </div>
          </Badge>
        </div>
      )}
    </>
  );
}
