import { useState, forwardRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Eye, EyeOff, Lock, Server, Network } from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';
import { api } from '@/lib/api';
// notifyError used by other pages; Login shows inline errors for better UX
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { LoadingButton } from '@/components/ui/loading-button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { cn } from '@/lib/utils';
import { BrandLogo } from '@/components/branding/BrandLogo';

// ---------------------------------------------------------------------------
// Validation Schemas
// ---------------------------------------------------------------------------

const loginSchema = z.object({
  username: z.string().min(1, 'Username is required'),
  password: z.string().min(1, 'Password is required'),
});

const changePasswordSchema = z
  .object({
    newPassword: z.string().min(8, 'Password must be at least 8 characters'),
    confirmPassword: z.string().min(1, 'Please confirm your password'),
  })
  .refine((data) => data.newPassword === data.confirmPassword, {
    message: 'Passwords do not match',
    path: ['confirmPassword'],
  });

type LoginFormValues = z.infer<typeof loginSchema>;
type ChangePasswordFormValues = z.infer<typeof changePasswordSchema>;

// ---------------------------------------------------------------------------
// Brand Panel (left side on desktop, top on mobile)
// ---------------------------------------------------------------------------

function BrandPanel() {
  return (
    <div className="relative hidden lg:flex lg:w-1/2 flex-col justify-between p-12 overflow-hidden bg-card border-r border-border">
      {/* Top: F5 Logo + tagline */}
      <div className="relative z-10">
        <div className="flex items-center gap-3 mb-6">
          <BrandLogo className="w-12 h-12 flex-shrink-0 text-foreground" aria-hidden="true" />
          <div>
            <h1 className="text-2xl font-bold text-foreground">
              BNK-Forge
            </h1>
            <p className="text-sm text-muted-foreground">
              v{__APP_VERSION__}
            </p>
          </div>
        </div>
        <p className="text-lg font-medium leading-relaxed max-w-md text-foreground/80">
          Deploy, Operate, and Monitor F5 BIG-IP Next for Kubernetes
        </p>
      </div>

      {/* Bottom: Feature highlights */}
      <div className="relative z-10 space-y-4">
        <FeatureItem
          icon={Server}
          title="Multi-Engine Deployment"
          desc="OpenTofu, Kubernetes Direct, and Operator engines"
        />
        <FeatureItem
          icon={Network}
          title="Fleet Management"
          desc="Manage network functions across clusters"
        />
        <FeatureItem
          icon={Lock}
          title="Enterprise Security"
          desc="RBAC, encryption at rest, audit trail"
        />
      </div>
    </div>
  );
}

function FeatureItem({
  icon: Icon,
  title,
  desc,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  desc: string;
}) {
  return (
    <div className="flex items-start gap-3">
      <div className="flex items-center justify-center w-8 h-8 rounded-lg mt-0.5 shrink-0 bg-primary/10">
        <Icon className="w-4 h-4 text-primary" />
      </div>
      <div>
        <p className="text-sm font-medium text-foreground">
          {title}
        </p>
        <p className="text-xs text-muted-foreground">
          {desc}
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Password Input with Visibility Toggle
// ---------------------------------------------------------------------------

const PasswordInput = forwardRef<HTMLInputElement, {
  id: string;
  error?: string;
  label: string;
  placeholder: string;
  autoComplete?: string;
  autoFocus?: boolean;
  name?: string;
  onChange?: React.ChangeEventHandler<HTMLInputElement>;
  onBlur?: React.FocusEventHandler<HTMLInputElement>;
}>(function PasswordInput({ id, error, label, placeholder, autoComplete, autoFocus, ...registration }, ref) {
  const [showPassword, setShowPassword] = useState(false);

  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <div className="relative">
        <Input
          id={id}
          ref={ref}
          type={showPassword ? 'text' : 'password'}
          placeholder={placeholder}
          autoComplete={autoComplete}
          autoFocus={autoFocus}
          className={cn('pr-10', error && 'border-destructive focus-visible:ring-destructive')}
          {...registration}
        />
        <button
          type="button"
          tabIndex={-1}
          onClick={() => setShowPassword(!showPassword)}
          className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
          aria-label={showPassword ? 'Hide password' : 'Show password'}
        >
          {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </button>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  );
});

// ---------------------------------------------------------------------------
// Login Form
// ---------------------------------------------------------------------------

function LoginForm({
  onMustChangePassword,
}: {
  onMustChangePassword: (currentPassword: string) => void;
}) {
  const [error, setError] = useState('');
  const navigate = useNavigate();
  const { login } = useAuthStore();

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginFormValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { username: '', password: '' },
  });

  const onSubmit = async (data: LoginFormValues) => {
    setError('');
    try {
      const result = await api.login(data.username, data.password);

      if (result.must_change_password) {
        login(result.token, result.user);
        onMustChangePassword(data.password);
        return;
      }

      login(result.token, result.user);
      navigate('/');
    } catch (err: unknown) {
      // Extract error message for inline display
      const axiosError = err as { response?: { data?: { error?: { message?: string } } } };
      const message =
        axiosError.response?.data?.error?.message || 'Login failed. Check your credentials.';
      setError(message);
    }
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-5">
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="space-y-2">
        <Label htmlFor="username">Username</Label>
        <Input
          id="username"
          type="text"
          placeholder="Enter username"
          autoComplete="username"
          autoFocus
          className={cn(errors.username && 'border-destructive focus-visible:ring-destructive')}
          {...register('username')}
        />
        {errors.username && (
          <p className="text-sm text-destructive">{errors.username.message}</p>
        )}
      </div>

      <PasswordInput
        id="password"
        label="Password"
        placeholder="Enter password"
        autoComplete="current-password"
        error={errors.password?.message}
        {...register('password')}
      />

      <LoadingButton
        type="submit"
        className="w-full"
        size="lg"
        loading={isSubmitting}
        loadingText="Signing in…"
      >
        Sign In
      </LoadingButton>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Change Password Form
// ---------------------------------------------------------------------------

function ChangePasswordForm({ currentPassword }: { currentPassword: string }) {
  const [error, setError] = useState('');
  const navigate = useNavigate();

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<ChangePasswordFormValues>({
    resolver: zodResolver(changePasswordSchema),
    defaultValues: { newPassword: '', confirmPassword: '' },
  });

  const onSubmit = async (data: ChangePasswordFormValues) => {
    setError('');
    try {
      await api.changePassword(currentPassword, data.newPassword);
      navigate('/');
    } catch (err: unknown) {
      const axiosError = err as { response?: { data?: { error?: { message?: string } } } };
      const message =
        axiosError.response?.data?.error?.message || 'Failed to change password';
      setError(message);
    }
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-5">
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <PasswordInput
        id="newPassword"
        label="New Password"
        placeholder="Enter new password"
        autoComplete="new-password"
        autoFocus
        error={errors.newPassword?.message}
        {...register('newPassword')}
      />

      <PasswordInput
        id="confirmPassword"
        label="Confirm Password"
        placeholder="Confirm new password"
        autoComplete="new-password"
        error={errors.confirmPassword?.message}
        {...register('confirmPassword')}
      />

      <LoadingButton
        type="submit"
        className="w-full"
        size="lg"
        loading={isSubmitting}
        loadingText="Changing password…"
      >
        Change Password
      </LoadingButton>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Main Login Page
// ---------------------------------------------------------------------------

export default function Login() {
  const [showChangePassword, setShowChangePassword] = useState(false);
  const [currentPassword, setCurrentPassword] = useState('');
  const handleMustChangePassword = (password: string) => {
    setCurrentPassword(password);
    setShowChangePassword(true);
  };

  return (
    <div className="min-h-screen flex bg-background">
      {/* Brand panel — visible on lg+ */}
      <BrandPanel />

      {/* Form panel */}
      <div className="flex-1 flex items-center justify-center p-6 sm:p-8 lg:p-12">
        <div className="w-full max-w-md motion-safe:animate-page-enter">
          {/* Mobile-only logo header */}
          <div className="lg:hidden text-center mb-8">
            <div className="inline-flex items-center justify-center w-14 h-14 mb-4">
              <BrandLogo className="w-12 h-12 text-foreground" aria-hidden="true" />
            </div>
            <h1 className="text-2xl font-bold text-foreground">BNK-Forge</h1>
            <p className="text-muted-foreground text-sm mt-1">
              F5 BIG-IP Next for Kubernetes
            </p>
          </div>

          <Card className="border-border">
            <CardHeader className="text-center pb-4">
              <CardTitle className="text-xl">
                {showChangePassword ? 'Change Password' : 'Sign In'}
              </CardTitle>
              <CardDescription>
                {showChangePassword
                  ? 'Please change your default password to continue'
                  : 'Enter your credentials to access BNK-Forge'}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {showChangePassword ? (
                <ChangePasswordForm currentPassword={currentPassword} />
              ) : (
                <LoginForm onMustChangePassword={handleMustChangePassword} />
              )}
            </CardContent>
          </Card>

          {/* Version footer */}
          <p className="text-center text-muted-foreground/50 text-xs mt-6">
            v{__APP_VERSION__}
          </p>
        </div>
      </div>
    </div>
  );
}
