/**
 * Transfer Ownership Dialog — MU-009
 *
 * Allows project owner or admin to transfer project ownership to another user.
 * Shows a searchable list of active users to pick the new owner.
 */
import { useState } from 'react';

import { Loader2, Shield, UserCheck } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useUsers } from '@/hooks/useUsers';
import { useTransferOwnership } from '@/hooks/useProjects';
import { useAuthStore } from '@/stores/authStore';
import { cn } from '@/lib/utils';
import type { Project, UserRole } from '@/types';

const ROLE_COLORS: Record<UserRole, string> = {
  admin: 'text-destructive',
  operator: 'text-primary',
  viewer: 'text-muted-foreground',
};

interface TransferOwnershipDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project: Project | null;
}

export function TransferOwnershipDialog({
  open,
  onOpenChange,
  project,
}: TransferOwnershipDialogProps) {
  const currentUser = useAuthStore((s) => s.user);
  const { data: usersData, isLoading: usersLoading } = useUsers();
  const transferMutation = useTransferOwnership();

  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);
  const [search, setSearch] = useState('');

  // Filter: show active users that aren't the current owner
  const users = (usersData?.users ?? []).filter(
    (u) =>
      u.is_active &&
      u.id !== project?.user_id &&
      (u.username.toLowerCase().includes(search.toLowerCase()) ||
        u.email.toLowerCase().includes(search.toLowerCase()))
  );

  const selectedUser = usersData?.users.find((u) => u.id === selectedUserId);

  const handleTransfer = async () => {
    if (!project || !selectedUserId) return;
    try {
      await transferMutation.mutateAsync({
        projectId: project.id,
        newOwnerId: selectedUserId,
      });
      onOpenChange(false);
      setSelectedUserId(null);
      setSearch('');
    } catch {
      // Error handled by mutation
    }
  };

  const handleOpenChange = (v: boolean) => {
    if (!v) {
      setSelectedUserId(null);
      setSearch('');
    }
    onOpenChange(v);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-[480px]">
        <DialogHeader>
          <DialogTitle>Transfer Ownership</DialogTitle>
          <DialogDescription>
            Transfer <strong>{project?.name}</strong> to another user.
            The new owner will have full control over this project.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* Current owner */}
          {project?.owner_username && (
            <div>
              <Label className="text-xs text-muted-foreground">Current Owner</Label>
              <div className="flex items-center gap-2 mt-1">
                <Shield className="h-4 w-4 text-primary" />
                <span className="text-sm font-medium text-foreground">
                  {project.owner_username}
                  {project.user_id === currentUser?.id && (
                    <Badge variant="outline" className="ml-2 text-xs">you</Badge>
                  )}
                </span>
              </div>
            </div>
          )}

          {/* Search */}
          <div>
            <Label htmlFor="transfer-search">New Owner</Label>
            <Input
              id="transfer-search"
              placeholder="Search users..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="mt-1"
              autoFocus
            />
          </div>

          {/* User list */}
          <div className="max-h-48 overflow-y-auto rounded-lg border border-border">
            {usersLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : users.length === 0 ? (
              <div className="py-6 text-center text-sm text-muted-foreground">
                {search ? 'No matching users found' : 'No other active users'}
              </div>
            ) : (
              users.map((u) => (
                <button
                  key={u.id}
                  type="button"
                  className={cn(
                    'w-full flex items-center gap-3 px-3 py-2.5 text-left transition-colors border-b border-border last:border-b-0',
                    selectedUserId === u.id
                      ? 'bg-primary/10'
                      : 'hover:bg-muted/50'
                  )}
                  onClick={() => setSelectedUserId(u.id)}
                >
                  <UserCheck
                    className={cn(
                      'h-4 w-4 flex-shrink-0',
                      selectedUserId === u.id ? 'text-primary' : 'text-muted-foreground'
                    )}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium truncate text-foreground">
                      {u.username}
                    </div>
                    <div className="text-xs text-muted-foreground truncate">{u.email}</div>
                  </div>
                  <Badge variant="outline" className={cn('text-xs flex-shrink-0', ROLE_COLORS[u.role])}>
                    {u.role}
                  </Badge>
                  {u.project_count > 0 && (
                    <span className="text-xs text-muted-foreground flex-shrink-0">
                      {u.project_count} projects
                    </span>
                  )}
                </button>
              ))
            )}
          </div>

          {/* Confirm selection */}
          {selectedUser && (
            <div className="rounded-lg border border-primary/20 bg-primary/10 p-3 text-sm">
              Transfer to <strong>{selectedUser.username}</strong> ({selectedUser.role}).
              They will become the sole owner of this project.
            </div>
          )}
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => handleOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={handleTransfer}
            disabled={!selectedUserId || transferMutation.isPending}
          >
            {transferMutation.isPending ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Transferring…
              </>
            ) : (
              'Transfer Ownership'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
