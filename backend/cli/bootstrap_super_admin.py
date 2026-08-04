import asyncio
import os
import sys
import argparse
import getpass
from uuid import UUID, uuid4
import json

# Ensure backend root is in import path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.core.config import settings
from backend.db.client import db_client
from backend.security.hashing import hash_password, validate_password_strength

# Canonical platform constants matching migrations
CANONICAL_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
CANONICAL_USER_ID = UUID("00000000-0000-0000-0000-000000000099")
CANONICAL_USER_TENANT_ID = UUID("00000000-0000-0000-0000-000000000098")
CANONICAL_EMAIL = "admin@docker.local"
CANONICAL_SLUG = "smiledent_clinic"
BOOTSTRAP_ADVISORY_LOCK_ID = 998877665544

def parse_args():
    parser = argparse.ArgumentParser(description="AWA Super-Admin Bootstrap Utility")
    parser.add_argument(
        "--password-file",
        type=str,
        help="Path to a mounted secret file containing the super-admin password"
    )
    return parser.parse_args()

def read_password(args) -> str:
    # 1. Password file input
    if args.password_file:
        try:
            with open(args.password_file, "r", encoding="utf-8") as f:
                pwd = f.read().strip()
                if not pwd:
                    raise ValueError("Password file is empty")
                return pwd
        except Exception as exc:
            print(f"Error: Failed to read password file: {exc}", file=sys.stderr)
            sys.exit(1)

    # 2. Interactive protected stdin prompt
    if sys.stdin.isatty():
        try:
            pwd = getpass.getpass("Enter super-admin bootstrap password: ")
            confirm = getpass.getpass("Confirm password: ")
            if pwd != confirm:
                print("Error: Passwords do not match", file=sys.stderr)
                sys.exit(1)
            if not pwd:
                print("Error: Password cannot be empty", file=sys.stderr)
                sys.exit(1)
            return pwd
        except Exception as exc:
            print(f"Error: Interactive password input failed: {exc}", file=sys.stderr)
            sys.exit(1)

    # 3. Environment variable fallback
    pwd = os.getenv("AWA_SUPER_ADMIN_BOOTSTRAP_PASSWORD")
    if pwd:
        return pwd

    print("Error: No password source provided. Use --password-file, stdin (interactive), or AWA_SUPER_ADMIN_BOOTSTRAP_PASSWORD env var.", file=sys.stderr)
    sys.exit(1)

async def bootstrap_provisioning(password: str) -> dict:
    # Validate password strength authoritative check
    try:
        validate_password_strength(password)
    except ValueError as exc:
        print(f"Error: Password strength requirements not met: {exc}", file=sys.stderr)
        sys.exit(1)

    # Database connectivity check
    try:
        await db_client.connect()
    except Exception as exc:
        print(f"Error: Database connectivity check failed: {exc}", file=sys.stderr)
        sys.exit(1)

    audit_event_id = uuid4()

    async with db_client.acquire() as conn:
        async with conn.transaction():
            # 1. Acquire transaction-level database lock
            await conn.execute("SELECT pg_advisory_xact_lock($1)", BOOTSTRAP_ADVISORY_LOCK_ID)

            # 2. Re-read and check if a valid credential already exists
            existing_cred = await conn.fetchval(
                """
                SELECT 1 FROM user_credentials uc
                JOIN user_tenants ut ON uc.user_id = ut.user_id
                WHERE ut.role = 'super_admin'
                LIMIT 1
                """
            )
            if existing_cred:
                return {
                    "status": "already_provisioned",
                    "super_admin_user_id": str(CANONICAL_USER_ID),
                    "platform_tenant_id": str(CANONICAL_TENANT_ID),
                    "role": "super_admin",
                    "audit_event_id": None
                }

            # 3. Verify tenant state
            tenant_row = await conn.fetchrow(
                "SELECT name, slug, plan_code, status, deleted_at FROM tenants WHERE id = $1",
                CANONICAL_TENANT_ID
            )
            if tenant_row:
                if tenant_row["deleted_at"] is not None:
                    raise RuntimeError("Canonical tenant is marked as deleted in database")
                if tenant_row["slug"] != CANONICAL_SLUG:
                    raise RuntimeError(f"Canonical tenant exists but has contradictory slug: {tenant_row['slug']}")
            else:
                # Provision tenant
                await conn.execute(
                    """
                    INSERT INTO tenants (id, name, slug, plan_code, status)
                    VALUES ($1, 'SmileDent Dental Clinic', $2, 'ENTERPRISE', 'active')
                    """,
                    CANONICAL_TENANT_ID,
                    CANONICAL_SLUG
                )

            # 4. Verify user state
            user_by_id = await conn.fetchrow(
                "SELECT email, deleted_at FROM users WHERE id = $1",
                CANONICAL_USER_ID
            )
            user_by_email = await conn.fetchrow(
                "SELECT id, deleted_at FROM users WHERE lower(email) = lower($1)",
                CANONICAL_EMAIL
            )
            if user_by_id:
                if user_by_id["deleted_at"] is not None:
                    raise RuntimeError("Canonical super-admin user is marked as deleted in database")
                if user_by_id["email"].lower() != CANONICAL_EMAIL.lower():
                    raise RuntimeError(f"Canonical user ID has contradictory email in database: {user_by_id['email']}")

            if user_by_email:
                if user_by_email["deleted_at"] is not None:
                    raise RuntimeError("Canonical super-admin email belongs to deleted user in database")
                if user_by_email["id"] != CANONICAL_USER_ID:
                    raise RuntimeError(f"Canonical super-admin email belongs to contradictory user ID: {user_by_email['id']}")

            if not user_by_id and not user_by_email:
                # Provision user
                await conn.execute(
                    """
                    INSERT INTO users (id, phone_number, email, name)
                    VALUES ($1, '+15550000099', $2, 'Docker Admin')
                    """,
                    CANONICAL_USER_ID,
                    CANONICAL_EMAIL
                )

            # 5. Verify user_tenants role link
            role_row = await conn.fetchrow(
                "SELECT role FROM user_tenants WHERE user_id = $1 AND tenant_id = $2",
                CANONICAL_USER_ID,
                CANONICAL_TENANT_ID
            )
            if role_row:
                if role_row["role"] != "super_admin":
                    raise RuntimeError(f"Canonical user-tenant mapping exists but has contradictory role: {role_row['role']}")
            else:
                # Provision user_tenants role link
                await conn.execute(
                    """
                    INSERT INTO user_tenants (id, user_id, tenant_id, role)
                    VALUES ($1, $2, $3, 'super_admin')
                    """,
                    CANONICAL_USER_TENANT_ID,
                    CANONICAL_USER_ID,
                    CANONICAL_TENANT_ID
                )

            # 6. Authoritative hashing service
            pwd_hash = await hash_password(password)

            # 7. Create/save credential
            await conn.execute(
                """
                INSERT INTO user_credentials (user_id, password_hash, password_algorithm)
                VALUES ($1, $2, 'bcrypt')
                ON CONFLICT (user_id) DO UPDATE SET password_hash = EXCLUDED.password_hash, updated_at = NOW()
                """,
                CANONICAL_USER_ID,
                pwd_hash
            )

            # 8. Write audit record
            await conn.execute(
                """
                INSERT INTO audit_logs (id, tenant_id, actor_id, action, resource_type, details, created_at)
                VALUES ($1, $2, $3, 'super_admin_bootstrapped', 'system', $4::jsonb, NOW())
                """,
                audit_event_id,
                CANONICAL_TENANT_ID,
                CANONICAL_USER_ID,
                json.dumps({"email": CANONICAL_EMAIL, "action": "bootstrap_super_admin"})
            )

            return {
                "status": "success",
                "super_admin_user_id": str(CANONICAL_USER_ID),
                "platform_tenant_id": str(CANONICAL_TENANT_ID),
                "role": "super_admin",
                "audit_event_id": str(audit_event_id)
            }

def main():
    # Verify Gating - Fail closed
    bootstrap_enabled = os.getenv("AWA_SUPER_ADMIN_BOOTSTRAP_ENABLED")
    if str(bootstrap_enabled).lower() != "true":
        print("Error: Super-admin bootstrap utility is disabled. Set AWA_SUPER_ADMIN_BOOTSTRAP_ENABLED=true to enable.", file=sys.stderr)
        sys.exit(1)

    # Verify authoritative authentication mode
    if settings.effective_auth_mode != "postgres":
        print(f"Error: Authoritative authentication mode must be postgres to support local credentials. Current mode: {settings.effective_auth_mode}", file=sys.stderr)
        sys.exit(1)

    args = parse_args()
    password = read_password(args)

    # Run provisioning inside asyncio loop
    try:
        result = asyncio.run(bootstrap_provisioning(password))
        print(json.dumps(result, indent=2))
    except Exception as exc:
        print(f"Error: Provisioning failed: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        # Clear secret material from local variables and environment best-effort
        if "password" in locals():
            del password
        if "AWA_SUPER_ADMIN_BOOTSTRAP_PASSWORD" in os.environ:
            del os.environ["AWA_SUPER_ADMIN_BOOTSTRAP_PASSWORD"]

if __name__ == "__main__":
    main()
