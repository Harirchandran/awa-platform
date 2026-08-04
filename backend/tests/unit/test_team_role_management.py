from uuid import uuid4
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from backend.api.app import app
from backend.api.dependencies import require_tenant_admin
TENANT_ID = uuid4()
ACTOR_ID = uuid4()
MEMBER_ID = uuid4()
async def override_tenant_admin():
    return {"user_id": ACTOR_ID, "tenant_id": TENANT_ID, "role": "tenant_admin"}
class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn
    async def __aenter__(self):
        return self.conn
    async def __aexit__(self, exc_type, exc, tb):
        return False
class FakeTeamConn:
    def __init__(self, role="tenant_admin", remaining_admins=1):
        self.role = role
        self.remaining_admins = remaining_admins
        self.updated = None
        self.deleted = None
    async def fetch(self, query, *args):
        return [
            {
                "user_id": ACTOR_ID,
                "email": "owner@example.test",
                "name": "Owner",
                "role": "tenant_admin",
                "created_at": None,
            },
            {
                "user_id": MEMBER_ID,
                "email": "member@example.test",
                "name": "Member",
                "role": self.role,
                "created_at": None,
            },
        ]
    async def fetchrow(self, query, *args):
        if "SELECT role FROM user_tenants" in query:
            return {"role": self.role}
        if "UPDATE user_tenants" in query:
            self.updated = args
            self.role = args[2]
            return {"user_id": args[1], "tenant_id": args[0], "role": args[2]}
        if "DELETE FROM user_tenants" in query:
            self.deleted = args
            return {"user_id": args[1], "tenant_id": args[0], "role": self.role}
        return None
    async def fetchval(self, query, *args):
        return self.remaining_admins
def test_list_team_members_is_tenant_scoped():
    conn = FakeTeamConn(role="operator")
    app.dependency_overrides[require_tenant_admin] = override_tenant_admin
    try:
        with patch("backend.api.routes.team.db_client.acquire", return_value=FakeAcquire(conn)):
            response = TestClient(app).get("/api/v1/team")
    finally:
        app.dependency_overrides.pop(require_tenant_admin, None)
    assert response.status_code == 200
    members = response.json()["data"]["members"]
    assert len(members) == 2
    assert members[0]["role"] == "tenant_admin"
def test_demoting_last_admin_is_blocked():
    conn = FakeTeamConn(role="tenant_admin", remaining_admins=0)
    app.dependency_overrides[require_tenant_admin] = override_tenant_admin
    try:
        with patch("backend.api.routes.team.db_client.acquire", return_value=FakeAcquire(conn)):
            response = TestClient(app).patch(
                f"/api/v1/team/{MEMBER_ID}/role",
                json={"role": "operator", "reason": "least_privilege"},
            )
    finally:
        app.dependency_overrides.pop(require_tenant_admin, None)
    assert response.status_code == 400
    assert "last tenant admin" in str(response.json()).lower()
    assert conn.updated is None
def test_role_update_audits_change():
    conn = FakeTeamConn(role="operator", remaining_admins=1)
    audit = AsyncMock()
    app.dependency_overrides[require_tenant_admin] = override_tenant_admin
    try:
        with (
            patch("backend.api.routes.team.db_client.acquire", return_value=FakeAcquire(conn)),
            patch("backend.api.routes.team.db_client.write_audit_log", audit),
        ):
            response = TestClient(app).patch(
                f"/api/v1/team/{MEMBER_ID}/role",
                json={"role": "member", "reason": "access_change"},
            )
    finally:
        app.dependency_overrides.pop(require_tenant_admin, None)
    assert response.status_code == 200
    assert response.json()["data"]["member"]["role"] == "member"
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["action"] == "tenant_member_role_updated"
    assert audit.await_args.kwargs["details"]["reason"] == "access_change"
def test_self_removal_is_blocked():
    app.dependency_overrides[require_tenant_admin] = override_tenant_admin
    try:
        response = TestClient(app).delete(f"/api/v1/team/{ACTOR_ID}")
    finally:
        app.dependency_overrides.pop(require_tenant_admin, None)
    assert response.status_code == 400
    assert "cannot remove yourself" in str(response.json()).lower()
# ==================== TENANT INVITATION TESTS ====================
from datetime import datetime, timezone, timedelta
from backend.api.dependencies import get_authenticated_user_only
async def override_auth_user():
    return {"user_id": MEMBER_ID, "email": "invited@example.com"}
# 1. Admin/owner can create an invite; operators/members cannot
def test_create_invite_permissions():
    app.dependency_overrides[require_tenant_admin] = override_tenant_admin
    try:
        mock_invite = {
            "id": uuid4(),
            "tenant_id": TENANT_ID,
            "email": "invited@example.com",
            "role": "operator",
            "status": "pending",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=7),
        }
        async def mock_create(tenant_id, email, role, token_hash, invited_by_user_id, expires_at):
            return mock_invite
        with (
            patch("backend.api.routes.team.db_client.acquire") as mock_acq,
            patch("backend.api.routes.team.db_client.create_tenant_invitation", side_effect=mock_create),
            patch("backend.api.routes.team.db_client.write_audit_log") as mock_audit,
            patch("backend.api.routes.team.settings.ENV", "local")
        ):
            mock_acq.return_value.__aenter__.return_value.fetchrow = AsyncMock(return_value=None)
            response = TestClient(app).post(
                "/api/v1/team/invites",
                json={"email": "invited@example.com", "role": "operator"}
            )
            assert response.status_code == 200
            data = response.json()["data"]
            assert data["email"] == "invited@example.com"
            assert data["role"] == "operator"
            assert "token" in data  # exposed in non-production
            mock_audit.assert_called_once()
    finally:
        app.dependency_overrides.pop(require_tenant_admin, None)
# 2. Role escalation check
def test_create_invite_escalation_blocked():
    app.dependency_overrides[require_tenant_admin] = override_tenant_admin
    try:
        response = TestClient(app).post(
            "/api/v1/team/invites",
            json={"email": "invited@example.com", "role": "owner"}
        )
        assert response.status_code == 403
        assert "cannot invite a user to a role higher" in response.json()["error"]["message"].lower()
    finally:
        app.dependency_overrides.pop(require_tenant_admin, None)
# 3. Duplicate active pending invite check
def test_create_invite_duplicate_blocked():
    app.dependency_overrides[require_tenant_admin] = override_tenant_admin
    try:
        with patch("backend.api.routes.team.db_client.acquire") as mock_acq:
            mock_acq.return_value.__aenter__.return_value.fetchrow = AsyncMock(
                return_value={"id": uuid4()}
            )
            response = TestClient(app).post(
                "/api/v1/team/invites",
                json={"email": "invited@example.com", "role": "operator"}
            )
            assert response.status_code == 400
            assert "already exists" in response.json()["error"]["message"].lower()
    finally:
        app.dependency_overrides.pop(require_tenant_admin, None)
# 4. Dev vs Production token exposure
def test_create_invite_token_exposure_by_env():
    app.dependency_overrides[require_tenant_admin] = override_tenant_admin
    try:
        mock_invite = {
            "id": uuid4(),
            "tenant_id": TENANT_ID,
            "email": "invited@example.com",
            "role": "operator",
            "status": "pending",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=7),
        }
        with (
            patch("backend.api.routes.team.db_client.acquire") as mock_acq,
            patch("backend.api.routes.team.db_client.create_tenant_invitation", return_value=mock_invite),
            patch("backend.api.routes.team.db_client.write_audit_log"),
            patch("backend.api.routes.team.settings.ENV", "production"),
            patch("backend.api.routes.team.settings.ALLOW_MANUAL_INVITE_LINKS", False)
        ):
            mock_acq.return_value.__aenter__.return_value.fetchrow = AsyncMock(return_value=None)
            response = TestClient(app).post(
                "/api/v1/team/invites",
                json={"email": "invited@example.com", "role": "operator"}
            )
            assert response.status_code == 200
            assert "token" not in response.json()["data"]
        with (
            patch("backend.api.routes.team.db_client.acquire") as mock_acq,
            patch("backend.api.routes.team.db_client.create_tenant_invitation", return_value=mock_invite),
            patch("backend.api.routes.team.db_client.write_audit_log"),
            patch("backend.api.routes.team.settings.ENV", "production"),
            patch("backend.api.routes.team.settings.ALLOW_MANUAL_INVITE_LINKS", True)
        ):
            mock_acq.return_value.__aenter__.return_value.fetchrow = AsyncMock(return_value=None)
            response = TestClient(app).post(
                "/api/v1/team/invites",
                json={"email": "invited@example.com", "role": "operator"}
            )
            assert response.status_code == 200
            assert "token" in response.json()["data"]
    finally:
        app.dependency_overrides.pop(require_tenant_admin, None)
# 5. Revocation
def test_revoke_invite():
    app.dependency_overrides[require_tenant_admin] = override_tenant_admin
    try:
        invite_id = uuid4()
        mock_revoked = {
            "id": invite_id,
            "tenant_id": TENANT_ID,
            "email": "invited@example.com",
            "role": "operator",
            "status": "revoked",
        }
        with (
            patch("backend.api.routes.team.db_client.revoke_tenant_invitation", return_value=mock_revoked),
            patch("backend.api.routes.team.db_client.write_audit_log") as mock_audit
        ):
            response = TestClient(app).post(f"/api/v1/team/invites/{invite_id}/revoke")
            assert response.status_code == 200
            assert response.json()["data"]["revoked"] is True
            mock_audit.assert_called_once()
    finally:
        app.dependency_overrides.pop(require_tenant_admin, None)
# 6. Accept Invite Successfully
def test_accept_invite_success():
    app.dependency_overrides[get_authenticated_user_only] = override_auth_user
    try:
        from backend.api.routes.team import hash_invite_token
        raw_token = "some_secret_token"
        token_hash = hash_invite_token(raw_token)
        invite_id = uuid4()
        mock_invite = {
            "id": invite_id,
            "tenant_id": TENANT_ID,
            "email": "invited@example.com",
            "role": "operator",
            "token_hash": token_hash,
            "status": "pending",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=7),
        }
        with (
            patch("backend.api.routes.team.db_client.get_tenant_invitation_by_hash", return_value=mock_invite),
            patch("backend.api.routes.team.db_client.accept_tenant_invitation", return_value={"status": "accepted"}),
            patch("backend.api.routes.team.db_client.write_audit_log") as mock_audit
        ):
            response = TestClient(app).post(
                "/api/v1/team/invites/accept",
                json={"token": raw_token}
            )
            assert response.status_code == 200
            assert response.json()["data"]["accepted"] is True
            mock_audit.assert_called_once()
    finally:
        app.dependency_overrides.pop(get_authenticated_user_only, None)
# 7. Accept Invite Rejection Cases (leak protection / mismatch / invalid / expired / revoked)
def test_accept_invite_rejections():
    app.dependency_overrides[get_authenticated_user_only] = override_auth_user
    try:
        with patch("backend.api.routes.team.db_client.get_tenant_invitation_by_hash", return_value=None):
            response = TestClient(app).post(
                "/api/v1/team/invites/accept",
                json={"token": "invalid_token"}
            )
            assert response.status_code == 400
            assert "invalid, expired, or revoked" in response.json()["error"]["message"].lower()
        mock_expired = {
            "id": uuid4(),
            "tenant_id": TENANT_ID,
            "email": "invited@example.com",
            "role": "operator",
            "token_hash": "dummy_hash",
            "status": "pending",
            "expires_at": datetime.now(timezone.utc) - timedelta(days=1),
        }
        from backend.api.routes.team import hash_invite_token
        raw_token = "expired_token"
        mock_expired["token_hash"] = hash_invite_token(raw_token)
        with patch("backend.api.routes.team.db_client.get_tenant_invitation_by_hash", return_value=mock_expired):
            response = TestClient(app).post(
                "/api/v1/team/invites/accept",
                json={"token": raw_token}
            )
            assert response.status_code == 400
            assert "invalid, expired, or revoked" in response.json()["error"]["message"].lower()
        mock_revoked = {
            "id": uuid4(),
            "tenant_id": TENANT_ID,
            "email": "invited@example.com",
            "role": "operator",
            "token_hash": hash_invite_token("revoked_token"),
            "status": "revoked",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=7),
        }
        with patch("backend.api.routes.team.db_client.get_tenant_invitation_by_hash", return_value=mock_revoked):
            response = TestClient(app).post(
                "/api/v1/team/invites/accept",
                json={"token": "revoked_token"}
            )
            assert response.status_code == 400
            assert "invalid, expired, or revoked" in response.json()["error"]["message"].lower()
        mock_mismatch = {
            "id": uuid4(),
            "tenant_id": TENANT_ID,
            "email": "different@example.com",
            "role": "operator",
            "token_hash": hash_invite_token("mismatch_token"),
            "status": "pending",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=7),
        }
        with patch("backend.api.routes.team.db_client.get_tenant_invitation_by_hash", return_value=mock_mismatch):
            response = TestClient(app).post(
                "/api/v1/team/invites/accept",
                json={"token": "mismatch_token"}
            )
            assert response.status_code == 400
            assert "invalid, expired, or revoked" in response.json()["error"]["message"].lower()
    finally:
        app.dependency_overrides.pop(get_authenticated_user_only, None)