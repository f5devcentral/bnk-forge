// Task & Deployment types

import type { JsonValue } from './common';

export interface Deployment {
  id: number;
  module_id: number;
  action: string;
  status: string;
  started_at?: string;
  completed_at?: string;
  output?: string;
  error?: string;
}

export interface Task {
  id: number;
  celery_task_id: string;
  task_type: string;
  status: string;
  project_id: number;
  project_name?: string;
  module_id?: number;
  module_name?: string;
  created_at: string;
  started_at?: string;
  completed_at?: string;
  duration_seconds?: number;
  triggered_by?: string;
  command?: string;
  working_directory?: string;
  exit_code?: number;
  logs?: string;
  logs_truncated?: boolean;
  logs_full_size?: number;
  error?: string;
  meta_data?: Record<string, JsonValue>;
  archived?: boolean;
}

export interface TaskListResponse {
  tasks: Task[];
  total: number;
  limit: number;
  offset: number;
}

export interface TaskStatsResponse {
  period_days: number;
  total_tasks: number;
  by_status: Record<string, number>;
  by_type: Record<string, number>;
  average_duration_seconds: number;
  project_id?: number;
}
