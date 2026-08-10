/**
 * Runbook Automation API methods (UX-010)
 */
import { apiClient } from './client';
import type {
  RunbookListResponse,
  RunbookExecutionResult,
  RunbookExecuteRequest,
} from '@/types';

export const runbooksApi = {
  /** List all available runbooks with their step definitions */
  listRunbooks: () =>
    apiClient
      .get<RunbookListResponse>('/api/runbooks')
      .then((res) => res.data),

  /** Execute a runbook against a cluster */
  executeRunbook: (runbookId: string, data: RunbookExecuteRequest) =>
    apiClient
      .post<RunbookExecutionResult>(`/api/runbooks/${runbookId}/run`, data)
      .then((res) => res.data),
};
