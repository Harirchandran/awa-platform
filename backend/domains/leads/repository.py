import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID

import asyncpg

from backend.db.client import db_client


class LeadRepository:
    def __init__(self, db=db_client):
        self.db = db

    async def list_leads(
        self,
        tenant_id: UUID,
        cursor: Optional[str] = None,
        limit: int = 20,
        status: Optional[str] = None,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self.db.list_leads(tenant_id=tenant_id, cursor=cursor, limit=limit, status=status, search=search)

    async def get_lead(self, tenant_id: UUID, lead_id: UUID) -> Optional[Dict[str, Any]]:
        return await self.db.get_lead(tenant_id, lead_id)

    async def update_lead_status(self, tenant_id: UUID, lead_id: UUID, status: str) -> bool:
        return await self.db.update_lead_status(tenant_id, lead_id, status)

    async def capture_lead(
        self,
        tenant_id: UUID,
        customer_id: UUID,
        conversation_id: Optional[UUID],
        lead_data: Dict[str, Any],
        conn: Optional[asyncpg.Connection] = None,
    ) -> Dict[str, Any]:
        if conn is not None:
            return await self._capture_lead(tenant_id, customer_id, conversation_id, lead_data, conn)
        async with self.db.acquire() as acquired_conn:
            async with acquired_conn.transaction():
                return await self._capture_lead(tenant_id, customer_id, conversation_id, lead_data, acquired_conn)

    async def _capture_lead(
        self,
        tenant_id: UUID,
        customer_id: UUID,
        conversation_id: Optional[UUID],
        lead_data: Dict[str, Any],
        conn: asyncpg.Connection,
    ) -> Dict[str, Any]:
        name = lead_data.get("name")
        phone = lead_data.get("phone")
        email = lead_data.get("email")
        interest = lead_data.get("interest")
        preferred_contact_time = lead_data.get("preferred_contact_time")
        step_data = lead_data.get("step_data") or {}

        customer_row = await conn.fetchrow(
            """
            SELECT name, phone_number, email, source_channel, metadata
            FROM customers
            WHERE tenant_id = $1 AND id = $2
            FOR UPDATE
            """,
            tenant_id,
            customer_id,
        )

        db_name = customer_row["name"] if customer_row else None
        db_phone = customer_row["phone_number"] if customer_row else None
        db_email = customer_row["email"] if customer_row else None
        source_channel = customer_row["source_channel"] if customer_row else "public_chat"
        existing_meta_str = customer_row["metadata"] if customer_row else None
        existing_meta = (
            json.loads(existing_meta_str)
            if existing_meta_str and isinstance(existing_meta_str, str)
            else (existing_meta_str or {})
        )
        if not isinstance(existing_meta, dict):
            existing_meta = {}

        existing_meta["lead_capture"] = {
            "data": step_data,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "conversation_id": str(conversation_id) if conversation_id else None,
        }

        update_fields = ["metadata = $1::jsonb", "updated_at = NOW()"]
        update_args = [json.dumps(existing_meta)]
        arg_idx = 2
        if (not db_name or str(db_name).strip().lower() == "unknown") and name:
            update_fields.append(f"name = ${arg_idx}")
            update_args.append(name)
            arg_idx += 1
        if (not db_phone or str(db_phone).strip().lower() == "unknown") and phone:
            update_fields.append(f"phone_number = ${arg_idx}")
            update_args.append(phone)
            arg_idx += 1
        if (not db_email or str(db_email).strip().lower() == "unknown") and email:
            update_fields.append(f"email = ${arg_idx}")
            update_args.append(email)
            arg_idx += 1

        update_args.extend([tenant_id, customer_id])
        tenant_idx = arg_idx
        customer_idx = arg_idx + 1
        await conn.execute(
            f"""
            UPDATE customers
            SET {', '.join(update_fields)}
            WHERE tenant_id = ${tenant_idx} AND id = ${customer_idx}
            """,
            *update_args,
        )

        existing_lead = await conn.fetchrow(
            """
            SELECT id
            FROM leads
            WHERE tenant_id = $1 AND customer_id = $2 AND status = 'new'
            FOR UPDATE
            """,
            tenant_id,
            customer_id,
        )
        lead_metadata = {
            "preferred_contact_time": preferred_contact_time,
            "step_data": step_data,
        }
        if existing_lead:
            lead_id = existing_lead["id"]
            result = await conn.execute(
                """
                UPDATE leads SET
                    name = COALESCE($1, name),
                    phone = COALESCE($2, phone),
                    email = COALESCE($3, email),
                    interest = COALESCE($4, interest),
                    metadata = metadata || $5::jsonb,
                    updated_at = NOW()
                WHERE tenant_id = $6 AND id = $7 AND status = 'new'
                """,
                name,
                phone,
                email,
                interest,
                json.dumps(lead_metadata),
                tenant_id,
                lead_id,
            )
            if result != "UPDATE 1":
                raise RuntimeError("Lead concurrency conflict: open lead status changed concurrently")
            created = False
        else:
            lead_id = await conn.fetchval(
                """
                INSERT INTO leads (
                    tenant_id, customer_id, conversation_id, source_channel,
                    name, phone, email, interest, metadata, status
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, 'new'
                ) RETURNING id
                """,
                tenant_id,
                customer_id,
                conversation_id,
                source_channel,
                name,
                phone,
                email,
                interest,
                json.dumps(lead_metadata),
            )
            created = True

        return {
            "id": lead_id,
            "tenant_id": tenant_id,
            "customer_id": customer_id,
            "conversation_id": conversation_id,
            "source_channel": source_channel,
            "name": name,
            "phone": phone,
            "email": email,
            "interest": interest,
            "created": created,
        }


lead_repository = LeadRepository()
