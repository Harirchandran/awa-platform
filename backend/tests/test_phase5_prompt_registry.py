from datetime import datetime, timezone
from uuid import uuid4
import pytest
from backend.db.client import db_client
class FakeTransaction:
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        return False
class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn
    async def __aenter__(self):
        return self.conn
    async def __aexit__(self, exc_type, exc, tb):
        return False
class PromptConnection:
    def __init__(self):
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []
        self.fetchval_calls: list[tuple[str, tuple[object, ...]]] = []
        self.fetchrow_calls: list[tuple[str, tuple[object, ...]]] = []
    def transaction(self):
        return FakeTransaction()
    async def execute(self, query, *args):
        self.execute_calls.append((query, args))
        return "OK"
    async def fetchval(self, query, *args):
        self.fetchval_calls.append((query, args))
        return 3
    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        return {"id": uuid4(), "version": 3}
    async def fetch(self, query, *args):
        return []
class PromptListConnection:
    def transaction(self):
        return FakeTransaction()
    async def fetch(self, query, *args):
        now = datetime(2026, 5, 22, tzinfo=timezone.utc)
        return [
            {
                "key": "system_prompt",
                "body": "duplicate-old",
                "version": 2,
                "updated_at": now,
                "updated_by": "system",
                "description": "Global instruction",
            },
            {
                "key": "system_prompt",
                "body": "latest",
                "version": 3,
                "updated_at": now,
                "updated_by": "system",
                "description": "Global instruction",
            },
            {
                "key": "sales_agent",
                "body": "sales",
                "version": 1,
                "updated_at": now,
                "updated_by": "system",
                "description": "Global instruction",
            },
        ]
@pytest.mark.asyncio
async def test_set_global_prompt_uses_null_safe_scope(monkeypatch):
    conn = PromptConnection()
    monkeypatch.setattr(db_client, "acquire", lambda: FakeAcquire(conn))
    result = await db_client.set_prompt(None, "system_prompt", "new prompt")
    assert result["version"] == 3
    assert "IS NOT DISTINCT FROM $1" in conn.fetchval_calls[0][0]
    assert conn.fetchval_calls[0][1] == (None, "system_prompt")
    deactivate_calls = [call for call in conn.execute_calls if "UPDATE prompt_registry" in call[0]]
    assert len(deactivate_calls) == 1
    assert "IS NOT DISTINCT FROM $1" in deactivate_calls[0][0]
    assert deactivate_calls[0][1] == (None, "system_prompt")
    assert conn.fetchrow_calls[0][1] == (None, "system_prompt", "new prompt", 3)
@pytest.mark.asyncio
async def test_get_all_prompts_returns_one_current_prompt_per_key(monkeypatch):
    monkeypatch.setattr(db_client, "acquire", lambda: FakeAcquire(PromptListConnection()))
    prompts = await db_client.get_all_prompts()
    assert [prompt["key"] for prompt in prompts] == ["system_prompt", "sales_agent"]
    system_prompt = next(prompt for prompt in prompts if prompt["key"] == "system_prompt")
    assert system_prompt["body"] == "latest"
    assert system_prompt["version"] == 3