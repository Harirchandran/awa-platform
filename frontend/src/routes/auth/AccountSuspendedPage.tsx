import { useNavigate } from 'react-router';
import { AlertTriangle, LogOut } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { authClient } from '@/lib/auth';
import { clearDevMockToken } from '@/lib/dev-auth';
import { clearAccountSuspendedMarker } from '@/lib/account-status';

export function AccountSuspendedPage() {
  const navigate = useNavigate();

  const handleSignOut = async () => {
    clearDevMockToken();
    clearAccountSuspendedMarker();
    localStorage.removeItem('awa_onboarding');
    await authClient.logout();
    navigate('/login', { replace: true });
  };

  return (
    <main className="flex min-h-[100dvh] items-center justify-center bg-background px-4 py-10">
      <section className="w-full max-w-md text-center">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-md border border-destructive/25 bg-destructive/10">
          <AlertTriangle className="h-5 w-5 text-destructive" />
        </div>
        <h1 className="mt-5 text-lg font-semibold text-foreground">Account suspended</h1>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          This business account is currently suspended. Contact your administrator to restore access.
        </p>
        <div className="mt-6 flex flex-col justify-center gap-2 sm:flex-row">
          <Button type="button" onClick={handleSignOut}>
            <LogOut className="h-4 w-4" />
            Sign out
          </Button>
          <Button type="button" variant="outline" onClick={() => navigate('/login', { replace: true })}>
            Back to login
          </Button>
        </div>
      </section>
    </main>
  );
}
