// Common / shared base types used across all domains

// Generic JSON-compatible value type (replaces 'any' for JSON values)
export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

export interface ApiResponse<T> {
  success: boolean;
  data?: T;
  error?: ApiError;
}

export interface ApiError {
  code: string;
  message: string;
  details?: Record<string, JsonValue>;
  path?: string;
}

// Error action — attached to parsed errors to provide clickable fix-it buttons
export interface ErrorAction {
  /** Button label shown in the toast (e.g., "Upload Now", "Connect Cluster") */
  label: string;
  /** Route to navigate to when clicked (e.g., "/projects/5", "/operators") */
  route?: string;
  /** Callback to invoke when clicked (e.g., trigger deployment API call) */
  callback?: () => void;
}

// WebSocket Message Types
export interface WSMessage {
  type: string;
  module_id?: number;
  status?: string;
  output?: string;
  error?: string;
}
