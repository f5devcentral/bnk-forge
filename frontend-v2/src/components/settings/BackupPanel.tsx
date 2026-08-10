/**
 * Backup & Restore panel for System settings
 */
import { useState, useRef, useEffect } from 'react';
import { SectionCard } from '@/components/ui/section-card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Loader2, Download, Upload, AlertTriangle, CheckCircle, Info, Eye, EyeOff } from 'lucide-react';
import { useCreateBackup, useRestoreBackup, useBackupStatus } from '@/hooks/useBackup';
import { RestoreOverlay } from './RestoreOverlay';
import { notify } from '@/lib/notify';
import { verifyBackupPassphrase } from '@/lib/verify-backup-passphrase';

export function BackupPanel() {
  const [backupPassphrase, setBackupPassphrase] = useState('');
  const [restorePassphrase, setRestorePassphrase] = useState('');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [confirmRestore, setConfirmRestore] = useState(false);
  const [showBackupPassphrase, setShowBackupPassphrase] = useState(false);
  const [showRestorePassphrase, setShowRestorePassphrase] = useState(false);
  const [restoreInProgress, setRestoreInProgress] = useState(false);
  const [restorePassphraseError, setRestorePassphraseError] = useState<string | null>(null);
  const [passphraseVerified, setPassphraseVerified] = useState<boolean | null>(null);
  const [passphraseVerifying, setPassphraseVerifying] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { data: backupStatus } = useBackupStatus();
  const createBackup = useCreateBackup();
  const restoreBackup = useRestoreBackup();

  // Client-side passphrase verification
  useEffect(() => {
    if (!selectedFile || restorePassphrase.length < 12) {
      setPassphraseVerified(null);
      return;
    }

    setPassphraseVerifying(true);
    setPassphraseVerified(null);
    setRestorePassphraseError(null);

    let cancelled = false;
    const verify = async () => {
      try {
        const valid = await verifyBackupPassphrase(selectedFile, restorePassphrase);
        if (!cancelled) {
          setPassphraseVerified(valid);
          if (!valid) {
            setRestorePassphraseError('Incorrect passphrase');
            setConfirmRestore(false);
          } else {
            setRestorePassphraseError(null);
          }
        }
      } catch {
        // Not a valid archive or can't parse — let the server handle it
        if (!cancelled) setPassphraseVerified(null);
      } finally {
        if (!cancelled) setPassphraseVerifying(false);
      }
    };

    // Debounce 500ms
    const timer = setTimeout(verify, 500);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [selectedFile, restorePassphrase]);

  const handleCreateBackup = () => {
    if (backupPassphrase.length < 12) {
      return; // Button should be disabled, but safety check
    }
    createBackup.mutate(backupPassphrase, {
      onSuccess: () => {
        setBackupPassphrase('');
      },
    });
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      setSelectedFile(file);
      setConfirmRestore(false);
      setPassphraseVerified(null);
    }
  };

  const handleRestore = () => {
    if (!selectedFile || restorePassphrase.length < 12) {
      return;
    }
    setRestorePassphraseError(null);
    restoreBackup.mutate(
      { file: selectedFile, passphrase: restorePassphrase },
      {
        onSuccess: (data) => {
          if (data.status !== 'success') return;

          // Show the overlay immediately
          setRestoreInProgress(true);

          // The backend will restart ~3s from now.
          // Wait 12s to cover the full restart cycle, then poll
          // health until OK, then reload with cache bust.
          const waitAndReload = async () => {
            await new Promise((r) => setTimeout(r, 12000));

            // Poll until backend is back
            for (;;) {
              try {
                const res = await fetch('/api/system/health', {
                  signal: AbortSignal.timeout(5000),
                  cache: 'no-store',
                });
                if (res.ok) break;
              } catch {
                // still restarting
              }
              await new Promise((r) => setTimeout(r, 2000));
            }

            // Small buffer then reload
            await new Promise((r) => setTimeout(r, 1000));
            const url = new URL(window.location.href);
            url.searchParams.set('_restored', Date.now().toString());
            window.location.replace(url.toString());
          };
          waitAndReload();
        },
        onError: (error: Error) => {
          // Detect passphrase error from backend response
          const errMsg = error.message?.toLowerCase() ?? '';
          const isAxiosError = error as unknown as { response?: { data?: { detail?: string; error?: { code?: string } } } };
          const errorCode = isAxiosError.response?.data?.error?.code;
          const detail = isAxiosError.response?.data?.detail?.toLowerCase() ?? '';

          if (errorCode === 'invalid_passphrase' || errMsg.includes('passphrase') || detail.includes('passphrase')) {
            setRestorePassphraseError('Incorrect passphrase. Please check and try again.');
            setConfirmRestore(false);
          } else {
            notify.error('Restore failed', error.message, { category: 'system' });
          }
        },
      }
    );
  };

  const isOperationInProgress =
    backupStatus?.in_progress || createBackup.isPending || restoreBackup.isPending;

  return (
    <div className="space-y-6">
      <RestoreOverlay
        active={restoreInProgress}
        tableCounts={restoreBackup.data?.table_counts}
        warnings={restoreBackup.data?.warnings}
        migrationsApplied={restoreBackup.data?.migrations_applied}
      />
      {/* Status Alert */}
      {backupStatus?.in_progress && (
        <Alert>
          <Loader2 className="h-4 w-4 animate-spin" />
          <AlertTitle>Operation in Progress</AlertTitle>
          <AlertDescription>
            {backupStatus.message || `${backupStatus.operation} operation is running...`}
          </AlertDescription>
        </Alert>
      )}

      {/* Create Backup */}
      <SectionCard title="Create backup">
        <p className="text-sm text-muted-foreground -mt-1 mb-4">
          Create a backup archive containing the database and encryption key. The backup is
          protected by your passphrase.
        </p>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="backup-passphrase">Passphrase (minimum 12 characters)</Label>
            <div className="relative">
              <Input
                id="backup-passphrase"
                type={showBackupPassphrase ? 'text' : 'password'}
                placeholder="Enter a strong passphrase"
                value={backupPassphrase}
                onChange={(e) => setBackupPassphrase(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && backupPassphrase.length >= 12 && !isOperationInProgress) {
                    handleCreateBackup();
                  }
                }}
                disabled={isOperationInProgress}
                className="pr-10"
              />
              <button
                type="button"
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                onClick={() => setShowBackupPassphrase(!showBackupPassphrase)}
                aria-label={showBackupPassphrase ? 'Hide passphrase' : 'Show passphrase'}
                tabIndex={-1}
              >
                {showBackupPassphrase ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
            {backupPassphrase.length > 0 && backupPassphrase.length < 12 && (
              <p className="text-sm text-destructive">Passphrase must be at least 12 characters</p>
            )}
          </div>
          <Button
            onClick={handleCreateBackup}
            disabled={backupPassphrase.length < 12 || isOperationInProgress}
          >
            {createBackup.isPending ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Creating Backup...
              </>
            ) : (
              <>
                <Download className="mr-2 h-4 w-4" />
                Create Backup
              </>
            )}
          </Button>
        </div>
      </SectionCard>

      {/* Restore Backup */}
      <SectionCard title="Restore backup">
        <p className="text-sm text-muted-foreground -mt-1 mb-4">
          Restore from a backup archive. This will replace all data in the current database.
        </p>
        <div className="space-y-4">
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>Warning: Destructive Operation</AlertTitle>
            <AlertDescription>
              Restoring a backup will replace ALL data in the database. The system will restart
              automatically after restore completes.
            </AlertDescription>
          </Alert>

          <div className="space-y-2">
            <Label htmlFor="restore-file">Backup Archive</Label>
            <Input
              ref={fileInputRef}
              id="restore-file"
              type="file"
              accept="*/*"
              onChange={handleFileSelect}
              disabled={isOperationInProgress}
            />
            {selectedFile && (
              <p className="text-sm text-muted-foreground">
                Selected: {selectedFile.name} ({(selectedFile.size / 1024 / 1024).toFixed(2)} MB)
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="restore-passphrase">Archive Passphrase</Label>
            <div className="relative">
              <Input
                id="restore-passphrase"
                type={showRestorePassphrase ? 'text' : 'password'}
                placeholder="Enter the backup passphrase"
                value={restorePassphrase}
                onChange={(e) => {
                  setRestorePassphrase(e.target.value);
                  if (restorePassphraseError) setRestorePassphraseError(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && selectedFile && passphraseVerified === true && !isOperationInProgress) {
                    if (confirmRestore) {
                      handleRestore();
                    } else {
                      setConfirmRestore(true);
                    }
                  }
                }}
                disabled={isOperationInProgress}
                className="pr-20"
              />
              <div className="absolute right-3 top-1/2 -translate-y-1/2 flex items-center gap-2">
                {passphraseVerifying && (
                  <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                )}
                {!passphraseVerifying && passphraseVerified === true && (
                  <CheckCircle className="h-4 w-4 text-success" />
                )}
                <button
                  type="button"
                  className="text-muted-foreground hover:text-foreground"
                  onClick={() => setShowRestorePassphrase(!showRestorePassphrase)}
                  aria-label={showRestorePassphrase ? 'Hide passphrase' : 'Show passphrase'}
                  tabIndex={-1}
                >
                  {showRestorePassphrase ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>
            {restorePassphraseError && (
              <p className="text-sm text-destructive">{restorePassphraseError}</p>
            )}
          </div>

          {selectedFile && passphraseVerified === true && !restorePassphraseError && !confirmRestore && (
            <Button
              variant="outline"
              onClick={() => setConfirmRestore(true)}
              disabled={isOperationInProgress}
            >
              Review Restore
            </Button>
          )}

          {confirmRestore && (
            <Alert>
              <Info className="h-4 w-4" />
              <AlertTitle>Confirm Restore</AlertTitle>
              <AlertDescription>
                You are about to restore from <strong>{selectedFile?.name}</strong>. This action
                cannot be undone. Are you sure?
              </AlertDescription>
            </Alert>
          )}

          {confirmRestore && (
            <div className="flex gap-2">
              <Button
                variant="destructive"
                onClick={handleRestore}
                disabled={isOperationInProgress}
              >
                {restoreBackup.isPending ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Restoring...
                  </>
                ) : (
                  <>
                    <Upload className="mr-2 h-4 w-4" />
                    Confirm Restore
                  </>
                )}
              </Button>
              <Button
                variant="outline"
                onClick={() => setConfirmRestore(false)}
                disabled={isOperationInProgress}
              >
                Cancel
              </Button>
            </div>
          )}

          {restoreBackup.isSuccess && restoreBackup.data?.status === 'success' && (
            <Alert>
              <CheckCircle className="h-4 w-4 text-success" />
              <AlertTitle>Restore Complete</AlertTitle>
              <AlertDescription>
                <ul className="list-disc list-inside space-y-1">
                  {Object.entries(restoreBackup.data.table_counts)
                    .filter(([, v]) => v > 0)
                    .map(([table, count]) => (
                      <li key={table}>{count} {table.replace(/_/g, ' ')}</li>
                    ))}
                  {restoreBackup.data.migrations_applied.length > 0 && (
                    <li>{restoreBackup.data.migrations_applied.length} migrations applied</li>
                  )}
                  {restoreBackup.data.warnings.length > 0 && (
                    <li>Warnings: {restoreBackup.data.warnings.join(', ')}</li>
                  )}
                </ul>
                <p className="mt-2 font-medium">
                  The system is restarting. This page will refresh automatically.
                </p>
              </AlertDescription>
            </Alert>
          )}
        </div>
      </SectionCard>
    </div>
  );
}
