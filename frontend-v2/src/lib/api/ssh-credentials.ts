/**
 * SSH Credentials API methods
 *
 * First-class SSH/bastion credential management — orthogonal to cloud provider.
 */
import { apiClient } from './client';
import type { SSHCredential, SSHCredentialCreate } from '@/types';

interface SSHCredentialSetupRequest {
  name: string;
  description?: string;
  host: string;
  port?: number;
  username: string;
  password: string;
  is_default?: boolean;
}

export const sshCredentialsApi = {
  listSSHCredentials: () =>
    apiClient.get<SSHCredential[]>('/api/ssh-credentials').then((res) => res.data),

  getSSHCredential: (id: number) =>
    apiClient.get<SSHCredential>(`/api/ssh-credentials/${id}`).then((res) => res.data),

  createSSHCredential: (data: SSHCredentialCreate) =>
    apiClient.post<SSHCredential>('/api/ssh-credentials', data).then((res) => res.data),

  /** Auto-setup: connect with password, generate+install key, save credential. */
  setupSSHCredential: (data: SSHCredentialSetupRequest) =>
    apiClient.post<SSHCredential>('/api/ssh-credentials/setup', data).then((res) => res.data),

  updateSSHCredential: (id: number, data: Partial<SSHCredentialCreate>) =>
    apiClient.put<SSHCredential>(`/api/ssh-credentials/${id}`, data).then((res) => res.data),

  deleteSSHCredential: (id: number) =>
    apiClient.delete(`/api/ssh-credentials/${id}`).then((res) => res.data),

  testSSHCredential: (id: number) =>
    apiClient.post<{ success: boolean; message?: string; error?: string; hostname?: string; ssh_banner?: string; details?: string }>(
      `/api/ssh-credentials/${id}/test`
    ).then((res) => res.data),

  testAllSSHCredentials: () =>
    apiClient
      .post<{ tested: number; results: Array<{ id: number; success: boolean; message?: string; error?: string }> }>(
        '/api/ssh-credentials/test-all',
      )
      .then((res) => res.data),

  /** Server-side generate a new SSH key pair. Returns plaintext private + public keys. */
  generateKeyPair: (keyType: 'ed25519' | 'rsa' = 'ed25519') =>
    apiClient
      .post<{
        success: boolean;
        data: { private_key: string; public_key: string; key_type: string };
      }>('/api/cloud-auth/ssh/generate-key', { key_type: keyType })
      .then((res) => res.data.data),

  /** Probe remote host for kubeconfig via SSH. Returns base64 kubeconfig + discovered contexts. */
  probeKubeconfig: (id: number) =>
    apiClient.post<{
      success: boolean;
      error?: string;
      kubeconfig?: string;
      contexts?: Array<{
        name: string;
        cluster: string;
        user: string;
        api_server: string;
        is_current: boolean;
      }>;
      current_context?: string;
      host?: string;
    }>(`/api/ssh-credentials/${id}/probe-kubeconfig`).then((res) => res.data),
};
