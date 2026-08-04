import { Navigate, Outlet } from 'react-router-dom';
import { useCurrentUser } from '@/features/auth/hooks';
import { AwaSplash } from '@/components/brand/AwaSplash';
import { getRoleHome, hasAllowedRole, type UserRole } from '@/features/auth/roles';
interface RoleRouteProps {
  roles: readonly UserRole[];
  fallbackPath?: string;
}
export function RoleRoute({ roles, fallbackPath }: RoleRouteProps) {
  const { user, isLoading } = useCurrentUser();
  if (isLoading) {
    return <AwaSplash />;
  }
  if (!hasAllowedRole(user?.role, roles)) {
    return <Navigate to={fallbackPath ?? getRoleHome(user?.role)} replace />;
  }
  return <Outlet />;
}