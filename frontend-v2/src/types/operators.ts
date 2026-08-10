// Operator Management types
//
// Note: Registration token types (OperatorRegistrationToken, OperatorTokenCreated,
// CreateTokenRequest, GenerateInstallCommandRequest/Response) were removed in
// D3-CLEANUP. The kubeconfig-first fleet architecture made them obsolete.

export type OperatorConnectivityMode = 'direct_ws' | 'reverse_ssh' | 'polling' | 'ngrok_tunnel' | 'in_cluster';

export interface ConnectedOperator {
  id: number;
  operator_id: string;
  cluster_name: string;
  cluster_id: number | null;
  connectivity_mode: OperatorConnectivityMode;
  connectivity_config: Record<string, unknown>;
  labels: Record<string, string>;
  is_connected: boolean;
  status: 'registered' | 'connected' | 'disconnected' | 'error';
  operator_version: string | null;
  kubernetes_version: string | null;
  node_count: number | null;
  nodes_ready: number | null;
  last_heartbeat_at: string | null;
  last_connected_at: string | null;
  last_disconnected_at: string | null;
  disconnect_reason: string | null;
  last_health_at: string | null;
  commands_executed: number;
  commands_failed: number;
  uptime_seconds: number;
  created_at: string;
  last_health_report?: Record<string, unknown> | null;
  registration_token_name?: string | null;
}

export interface ConnectivityModeInfo {
  id: OperatorConnectivityMode;
  label: string;
  description: string;
  icon: string;
  requires_public_access: boolean;
  available: boolean;
  status_note: string | null;
}

export interface ConnectivitySetup {
  mode: OperatorConnectivityMode;
  control_plane_url: string;
  env_vars: Record<string, string>;
  helm_install_command: string;
  kubectl_command: string;
  notes: string[];
  setup_status: 'ready' | 'pending' | 'error';
  error: string | null;
}
