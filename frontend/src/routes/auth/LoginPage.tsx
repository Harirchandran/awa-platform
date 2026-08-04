import { LoginForm } from '@/features/auth/LoginForm';
export function LoginPage() {
  return (
    <div className="flex min-h-[100dvh] flex-col items-center justify-center bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-emerald-900/20 via-background to-background px-4 py-10 sm:py-12">
      <div className="motion-enter w-full max-w-sm rounded-lg border border-white/10 bg-card/80 p-5 shadow-2xl backdrop-blur-xl sm:p-6">
        <LoginForm />
      </div>
    </div>
  );
}
