import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import * as z from 'zod';
import { authClient } from '@/lib/auth';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Loader2 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import api from '@/lib/api';
import { getApiErrorMessage, isSuspendedTenantError } from '@/lib/api-errors';
import { markAccountSuspended } from '@/lib/account-status';
import { clearDevMockToken, isMockAuthEnabled, setDevMockToken } from '@/lib/dev-auth';
import { getRoleHome, type UserRole } from './roles';

const loginSchema = z.object({
  email: z.string().email({ message: 'Please enter a valid email address' }),
  password: z.string().min(8, { message: 'Password must be at least 8 characters' }),
});

type LoginFormValues = z.infer<typeof loginSchema>;

function usesLocalMockLoginBypass() {
  return isMockAuthEnabled();
}


export function LoginForm() {
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const navigate = useNavigate();
  const localMockLogin = usesLocalMockLoginBypass();

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<LoginFormValues>({
    resolver: zodResolver(loginSchema),
  });

  async function onSubmit(data: LoginFormValues) {
    setIsLoading(true);
    setError(null);
    try {
      if (localMockLogin) {
        try {
          const response = await api.post<{ access_token: string; role: UserRole }>('/api/v1/auth/mock-login', {
            email: data.email,
          });
          setDevMockToken(response.data.access_token);
          localStorage.removeItem('awa_onboarding');
          navigate(getRoleHome(response.data.role));
        } catch (error: unknown) {
          clearDevMockToken();
          setError(getApiErrorMessage(error, 'Mock login failed.'));
        }
        return;
      }
      const session = await authClient.login(data.email, data.password);
      if (session) {
        // Fetch current user from our backend to determine Role Routing
        // (App.tsx / RoleRoute handles the actual rerouting, but we can do an initial push)
        const me = await api.get<{ role: UserRole }>('/api/v1/me');
        navigate(getRoleHome(me.data.role));
      }
    } catch (error: unknown) {
      if (isSuspendedTenantError(error)) {
        clearDevMockToken();
        authClient.clearSession();
        localStorage.removeItem('awa_onboarding');
        markAccountSuspended();
        navigate('/account-suspended', { replace: true });
        return;
      }
      setError(getApiErrorMessage(error, 'An unexpected error occurred during login.'));
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <div className="space-y-5 w-full">
      <div className="flex flex-col space-y-1 text-center">
        <h1 className="text-[15px] font-semibold text-foreground">Sign in to AWA</h1>
        <p className="text-[13px] text-muted-foreground">
          Always Working Assistant
        </p>
      </div>
      {localMockLogin && (
        <Alert>
          <AlertDescription className="text-[13px] leading-5">
            Local mock auth mode is enabled. Format-valid credentials start a test session; production password rejection is not tested in this mode.
          </AlertDescription>
        </Alert>
      )}
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              placeholder="admin@tenant.com"
              disabled={isLoading}
              {...register('email')}
            />
            {errors.email && (
              <p className="text-sm text-destructive">{errors.email.message}</p>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              disabled={isLoading}
              {...register('password')}
            />
            {errors.password && (
              <p className="text-sm text-destructive">{errors.password.message}</p>
            )}
          </div>
        </div>
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <Button type="submit" className="w-full" disabled={isLoading}>
          {isLoading ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : null}
          Sign In
        </Button>
      </form>
      <p className="text-center text-[12px] text-muted-foreground">
        Don't have an account?{' '}
        <a href="/signup" className="text-foreground hover:underline">
          Sign up
        </a>
      </p>
    </div>
  );
}
