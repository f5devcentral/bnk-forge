/**
 * SSH connectivity probe hook.
 *
 * One-shot connectivity check for an SSH credential (typically a project
 * jumphost). Cached forever for the page lifetime — does not auto-refetch
 * on mount/focus/reconnect, since the probe itself is expensive and the
 * answer rarely changes within a single user session.
 */
import { useQuery } from '@tanstack/react-query';
import { sshCredentialsApi } from '@/lib/api/ssh-credentials';

export interface SSHConnectivityResult {
  success: boolean;
  message?: string;
  error?: string;
  hostname?: string;
  ssh_banner?: string;
  details?: string;
}

export function useSSHConnectivity(credentialId: number | null | undefined, enabled = true) {
  return useQuery<SSHConnectivityResult>({
    queryKey: ['ssh-credentials', 'test', credentialId],
    queryFn: () => sshCredentialsApi.testSSHCredential(credentialId as number),
    enabled: enabled && !!credentialId,
    staleTime: Infinity,
    gcTime: Infinity,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  });
}
