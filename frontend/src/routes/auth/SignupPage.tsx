import { useState } from 'react';
import { useNavigate, Link } from 'react-router';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import * as z from 'zod';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Loader2 } from 'lucide-react';
import { getApiErrorMessage } from '@/lib/api-errors';
import { authClient } from '@/lib/auth';
import { isMockAuthEnabled, setDevMockToken } from '@/lib/dev-auth';

const signupSchema = z.object({
  business_name: z.string().min(2, 'Business name must be at least 2 characters'),
  email: z.string().email('Please enter a valid email address'),
  password: z.string()
    .min(8, 'Password must be at least 8 characters')
    .regex(/[A-Za-z]/, 'Password must contain a letter')
    .regex(/\d/, 'Password must contain a number'),
});

type SignupFormValues = z.infer<typeof signupSchema>;

const BUSINESS_TYPE_OPTIONS = [
  {
    value: 'general',
    label: 'General Business',
    description: 'Starts with FAQ and handoff basics. Enable specific workflows later.',
  },
  {
    value: 'clinic',
    label: 'Clinic / Healthcare',
    description: 'Starts appointment-first with patient contact fields, reminders, reviews, support, and handoff.',
  },
  {
    value: 'salon',
    label: 'Salon / Beauty',
    description: 'Starts booking-first with reminders, reviews, lead capture, FAQ, and handoff.',
  },
  {
    value: 'restaurant',
    label: 'Restaurant / F&B',
    description: 'Starts order-first with catalog, payments, reminders, reviews, FAQ, and handoff.',
  },
  {
    value: 'retail',
    label: 'Retail / E-Commerce',
    description: 'Starts catalog/order-first with payments, lead capture, reminders, reviews, FAQ, and handoff.',
  },
  {
    value: 'education',
    label: 'Education',
    description: 'Starts inquiry and booking focused with reminders, reviews, lead capture, FAQ, and handoff.',
  },
  {
    value: 'services',
    label: 'Professional Services',
    description: 'Starts service-booking focused with support, lead capture, reminders, reviews, FAQ, and handoff.',
  },
];

export function SignupPage() {
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [businessType, setBusinessType] = useState('general');
  const selectedBusinessType = BUSINESS_TYPE_OPTIONS.find((option) => option.value === businessType) ?? BUSINESS_TYPE_OPTIONS[0];
  const navigate = useNavigate();
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<SignupFormValues>({
    resolver: zodResolver(signupSchema),
  });

  async function onSubmit(data: SignupFormValues) {
    setIsLoading(true);
    setError(null);
    try {
      const response = await authClient.register({
        business_name: data.business_name,
        email: data.email,
        password: data.password,
        business_type: businessType,
      });
      const result = response.data;
      localStorage.setItem('awa_onboarding', JSON.stringify({
        tenant_id: result.tenant_id,
        user_id: result.user_id,
        business_name: result.business_name,
        business_type: businessType,
        completed: false,
      }));
      if (result.dev_session_token && isMockAuthEnabled()) {
        setDevMockToken(result.dev_session_token);
        navigate('/onboarding');
        return;
      }
      if (!authClient.isAuthenticated()) {
        try {
          await authClient.login(data.email, data.password);
        } catch {
          navigate('/login');
          return;
        }
      }
      navigate('/onboarding');
    } catch (error: unknown) {
      setError(getApiErrorMessage(error, 'Registration failed. Please try again.'));
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <div className="flex min-h-[100dvh] flex-col items-center justify-center bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-emerald-900/20 via-background to-background px-4 py-10 sm:py-12">
      <div className="motion-enter w-full max-w-[380px] space-y-6">
        <div className="text-center">
          <p className="text-[14px] font-semibold text-foreground mb-1.5">Create your business</p>
          <p className="text-[13px] text-muted-foreground">Set up your Always Working Assistant</p>
        </div>
        <div className="rounded-lg border border-white/10 bg-card/80 p-5 shadow-2xl backdrop-blur-xl sm:p-6">
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="business_name" className="text-[12px]">Business Name</Label>
              <Input id="business_name" placeholder="e.g. SmileDent Dental Clinic" disabled={isLoading} {...register('business_name')} />
              {errors.business_name && <p className="text-[12px] text-destructive">{errors.business_name.message}</p>}
            </div>
            <div className="space-y-1.5">
              <Label className="text-[12px]">Business Type</Label>
              <Select value={businessType} onValueChange={setBusinessType}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {BUSINESS_TYPE_OPTIONS.map((option) => (
                    <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-[12px] leading-5 text-muted-foreground">{selectedBusinessType.description}</p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="email" className="text-[12px]">Email</Label>
              <Input id="email" type="email" placeholder="you@business.com" disabled={isLoading} {...register('email')} />
              {errors.email && <p className="text-[12px] text-destructive">{errors.email.message}</p>}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="password" className="text-[12px]">Password</Label>
              <Input id="password" type="password" placeholder="8+ chars with letters and numbers" disabled={isLoading} {...register('password')} />
              {errors.password && <p className="text-[12px] text-destructive">{errors.password.message}</p>}
            </div>
            {error && (
              <Alert variant="destructive">
                <AlertDescription className="text-[12px]">{error}</AlertDescription>
              </Alert>
            )}
            <Button type="submit" className="w-full text-[13px]" disabled={isLoading}>
              {isLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Create Account
            </Button>
          </form>
        </div>
        <p className="text-center text-[12px] text-muted-foreground">
          Already have an account?{' '}
          <Link to="/login" className="text-foreground hover:underline">Sign in</Link>
        </p>
      </div>
    </div>
  );
}
