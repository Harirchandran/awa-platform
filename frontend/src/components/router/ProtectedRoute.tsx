import { Navigate, Outlet } from 'react-router-dom';
import { useSession } from '@/features/auth/hooks';
import { AwaSplash } from '@/components/brand/AwaSplash';
export function ProtectedRoute() {
  const { session, isLoading } = useSession();
  if (isLoading) {
    return <AwaSplash />;
  }
  if (!session) {
    return <Navigate to="/login" replace />;
  }
  return <Outlet />;
}