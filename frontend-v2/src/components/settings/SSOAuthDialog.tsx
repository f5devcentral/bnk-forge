import { useState, useEffect, useRef, useCallback } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Card } from '@/components/ui/card';
import { Loader2, ExternalLink, CheckCircle, AlertCircle, Copy } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '@/lib/api';
import { notify } from '@/lib/notify';

interface SSOAuthDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  templateId: number;
  templateName: string;
  onSuccess?: () => void;
}

export function SSOAuthDialog({
  open,
  onOpenChange,
  templateId,
  templateName,
  onSuccess,
}: SSOAuthDialogProps) {
  const [step, setStep] = useState<'initiating' | 'waiting' | 'success' | 'error'>('initiating');
  const [deviceCode, setDeviceCode] = useState<string>('');
  const [userCode, setUserCode] = useState<string>('');
  const [verificationUri, setVerificationUri] = useState<string>('');
  const [verificationUriComplete, setVerificationUriComplete] = useState<string>('');
  const [expiresIn, setExpiresIn] = useState<number>(0);
  const [error, setError] = useState<string>('');
  const [pollInterval, setPollInterval] = useState<number>(5);

  // Use refs for polling state to avoid stale closure issues with setInterval
  const isPollingRef = useRef(false);
  const pollFailuresRef = useRef(0);

  // Track mount state so an in-flight initiate/poll request cannot setState
  // after the dialog is unmounted -- e.g. the user opens and immediately closes
  // it while authenticateTemplateSSO is still pending. React tolerates that
  // silently in the browser, but it is a genuine leak of a stale update, and
  // under vitest the environment is already torn down by the time the promise
  // settles ("ReferenceError: window is not defined" from dispatchSetState).
  // The polling effect below already guards itself with a `cancelled` flag;
  // this gives initiateSSO the same protection.
  const isMountedRef = useRef(true);
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const initiateSSO = useCallback(async () => {
    try {
      setError('');
      pollFailuresRef.current = 0;
      const response = await api.authenticateTemplateSSO(templateId);
      if (!isMountedRef.current) return;

      setDeviceCode(response.data.device_code);
      setUserCode(response.data.user_code);
      setVerificationUri(response.data.verification_uri);
      setVerificationUriComplete(response.data.verification_uri_complete);
      setExpiresIn(response.data.expires_in);
      setPollInterval(response.data.interval);
      setStep('waiting');
    } catch (err: unknown) {
      if (!isMountedRef.current) return;
      const message = isAxiosError(err)
        ? err.response?.data?.error?.message || err.response?.data?.detail || 'Failed to initiate SSO authentication'
        : 'Failed to initiate SSO authentication';
      setError(message);
      setStep('error');
    }
  }, [templateId]);

  const pollSSO = useCallback(async () => {
    // Prevent multiple simultaneous polls
    if (isPollingRef.current) {
      return;
    }

    isPollingRef.current = true;

    try {
      const response = await api.pollTemplateSSO(templateId, deviceCode);
      if (!isMountedRef.current) return;

      // Check if still pending (HTTP 202 returns pending: true)
      if (response.pending) {
        pollFailuresRef.current = 0; // Reset on valid pending response
        isPollingRef.current = false;
        return; // Keep polling
      }

      // Success — either fresh auth or already-authenticated dedup
      isPollingRef.current = false;
      setStep('success');
      notify.success('AWS SSO authentication successful!', undefined, { category: 'security' });
      onSuccess?.();

      // Close dialog after a moment
      setTimeout(() => {
        if (isMountedRef.current) onOpenChange(false);
      }, 2000);
    } catch (err: unknown) {
      isPollingRef.current = false;
      if (!isMountedRef.current) return;

      // If 202 status, it's still pending (shouldn't happen since 202 is success, but be safe)
      if (isAxiosError(err) && err.response?.status === 202) {
        pollFailuresRef.current = 0;
        return; // Keep polling
      }

      // Track poll failures - allow a few retries before showing error
      // This handles transient network issues or brief backend delays
      pollFailuresRef.current += 1;

      if (pollFailuresRef.current < 3) {
        return; // Keep polling, don't show error yet
      }

      // After 3 failures, show error
      const errorMessage = isAxiosError(err)
        ? err.response?.data?.error?.message || err.response?.data?.detail || 'Authentication failed'
        : 'Authentication failed';
      setError(errorMessage);
      setStep('error');
    }
  }, [templateId, deviceCode, onSuccess, onOpenChange]);

  // Initiate SSO flow when dialog opens
  useEffect(() => {
    if (open && step === 'initiating') {
      initiateSSO();
    }
  }, [open, step, initiateSSO]);

  // Poll for authentication completion using recursive setTimeout
  // to avoid stale closure issues with setInterval
  useEffect(() => {
    if (step !== 'waiting' || !deviceCode || !open) return;

    let cancelled = false;

    const poll = async () => {
      if (cancelled) return;
      await pollSSO();
      if (!cancelled) {
        timeoutId = setTimeout(poll, pollInterval * 1000);
      }
    };

    // Small delay before first poll to ensure backend is ready
    let timeoutId = setTimeout(poll, 1000);

    return () => {
      cancelled = true;
      clearTimeout(timeoutId);
      isPollingRef.current = false;
    };
  }, [step, deviceCode, open, pollInterval, pollSSO]);

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
  };

  const openVerificationUrl = () => {
    window.open(verificationUriComplete || verificationUri, '_blank');
  };

  const resetAndClose = () => {
    setStep('initiating');
    setDeviceCode('');
    setUserCode('');
    setVerificationUri('');
    setError('');
    isPollingRef.current = false;
    pollFailuresRef.current = 0;
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={resetAndClose}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>AWS SSO Authentication</DialogTitle>
          <DialogDescription>
            Authenticating template: {templateName}
          </DialogDescription>
        </DialogHeader>

        {step === 'initiating' && (
          <div className="flex flex-col items-center justify-center py-8 space-y-4">
            <Loader2 className="h-12 w-12 animate-spin text-primary" />
            <p className="text-sm text-muted-foreground">Initiating SSO flow...</p>
          </div>
        )}

        {step === 'waiting' && (
          <div className="space-y-4">
            <Alert>
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>
                Please complete authentication in your browser. This dialog will automatically close when done.
              </AlertDescription>
            </Alert>

            <Card className="p-4 space-y-4">
              <div>
                <p className="text-sm font-medium mb-2">Step 1: Open the verification URL</p>
                <Button
                  onClick={openVerificationUrl}
                  variant="outline"
                  className="w-full justify-between"
                >
                  <span className="truncate">{verificationUri}</span>
                  <ExternalLink className="h-4 w-4 ml-2 flex-shrink-0" />
                </Button>
              </div>

              <div>
                <p className="text-sm font-medium mb-2">Step 2: Enter this code</p>
                <div className="flex items-center gap-2">
                  <div className="flex-1 p-3 bg-muted rounded-md font-mono text-lg text-center font-bold">
                    {userCode}
                  </div>
                  <Button
                    onClick={() => copyToClipboard(userCode)}
                    variant="outline"
                    size="icon"
                    aria-label="Copy user code to clipboard"
                  >
                    <Copy className="h-4 w-4" aria-hidden="true" />
                  </Button>
                </div>
              </div>

              <div className="text-xs text-muted-foreground text-center">
                Code expires in {Math.floor(expiresIn / 60)} minutes
              </div>
            </Card>

            <div className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span>Waiting for authentication...</span>
            </div>
          </div>
        )}

        {step === 'success' && (
          <div className="flex flex-col items-center justify-center py-8 space-y-4">
            <CheckCircle className="h-16 w-16 text-success" />
            <p className="text-lg font-medium">Authentication Successful!</p>
            <p className="text-sm text-muted-foreground text-center">
              AWS SSO credentials have been stored securely.
            </p>
          </div>
        )}

        {step === 'error' && (
          <div className="space-y-4">
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>{error}</AlertDescription>
            </Alert>

            <div className="flex justify-end gap-2">
              <Button onClick={resetAndClose} variant="outline">
                Cancel
              </Button>
              <Button
                onClick={() => {
                  setStep('initiating');
                  setError('');
                  initiateSSO();
                }}
              >
                Try Again
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
