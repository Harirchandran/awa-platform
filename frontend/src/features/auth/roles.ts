export const USER_ROLES = [
  'member',
  'operator',
  'tenant_admin',
  'admin',
  'owner',
  'super_admin',
] as const;
export type UserRole = (typeof USER_ROLES)[number];
export const TENANT_READ_ROLES = [
  'member',
  'operator',
  'tenant_admin',
  'admin',
  'owner',
  'super_admin',
] as const satisfies readonly UserRole[];
export const TENANT_OPERATION_ROLES = [
  'operator',
  'tenant_admin',
  'admin',
  'owner',
  'super_admin',
] as const satisfies readonly UserRole[];
export const TENANT_ADMIN_ROLES = [
  'tenant_admin',
  'admin',
  'owner',
  'super_admin',
] as const satisfies readonly UserRole[];
export const SUPER_ADMIN_ROLES = ['super_admin'] as const satisfies readonly UserRole[];
export function hasAllowedRole(role: UserRole | null | undefined, allowedRoles: readonly UserRole[]) {
  return Boolean(role && allowedRoles.includes(role));
}
export function getRoleHome(role: UserRole | null | undefined) {
  if (role === 'super_admin') {
    return '/admin/tenants';
  }
  if (role && TENANT_READ_ROLES.includes(role)) {
    return '/dashboard';
  }
  return '/login';
}