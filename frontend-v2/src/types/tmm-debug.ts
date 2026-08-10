/**
 * TMM Debug Sidecar types — diagnostic commands in TMM debug containers.
 */

// ---------------------------------------------------------------------------
// Pod Discovery
// ---------------------------------------------------------------------------

export interface TMMDebugPod {
  name: string;
  namespace: string;
  has_debug: boolean;
  containers: string[];
  phase: string;
}

export interface TMMDebugPodsResponse {
  pods: TMMDebugPod[];
  count: number;
  debug_available: number;
}

// ---------------------------------------------------------------------------
// Command Execution
// ---------------------------------------------------------------------------

export interface TMMDebugExecRequest {
  pod_name: string;
  namespace?: string;
  command: string;
  timeout?: number;
}

export interface TMMDebugExecResponse {
  stdout: string;
  stderr: string;
  exit_code: number;
  duration_ms: number;
  command: string;
}

// ---------------------------------------------------------------------------
// tmctl — Structured Table Queries
// ---------------------------------------------------------------------------

export interface TMMDebugTmctlRequest {
  pod_name: string;
  namespace?: string;
  table: string;
  columns?: string[];
  width?: number;
  directory?: string;
}

export interface TMMDebugTmctlResponse {
  columns: string[];
  rows: string[][];
  raw: string;
  stderr: string;
  exit_code: number;
  duration_ms: number;
  command: string;
}

// ---------------------------------------------------------------------------
// configview
// ---------------------------------------------------------------------------

export interface TMMDebugConfigviewRequest {
  pod_name: string;
  namespace?: string;
  uuid: string;
}

export interface TMMDebugConfigviewUuidsRequest {
  pod_name: string;
  namespace?: string;
}

export interface TMMDebugConfigviewUuidsResponse {
  uuids: string[];
  raw: string;
  stderr: string;
  exit_code: number;
  duration_ms: number;
  command: string;
}

// ---------------------------------------------------------------------------
// bdt_cli — Networking Diagnostics
// ---------------------------------------------------------------------------

export interface TMMDebugBdtRequest {
  pod_name: string;
  namespace?: string;
  subcommand: string;
}

// ---------------------------------------------------------------------------
// Command Card Definitions
// ---------------------------------------------------------------------------

export type CommandCategory = 'tmctl' | 'bdt_cli' | 'configview' | 'netkvest' | 'raw';

export interface CommandCard {
  id: string;
  name: string;
  description: string;
  category: CommandCategory;
  icon?: string;
}
