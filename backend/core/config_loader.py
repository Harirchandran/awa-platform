import json
from uuid import UUID
from typing import Dict, Any, Optional
from backend.core.context import TenantConfig
from backend.schemas.config import normalize_tenant_config
class ConfigLoader:
    def __init__(self, db_client=None):
        self.db = db_client # Mock for now
    async def load_config(self, tenant_id: UUID, version: Optional[int] = None) -> TenantConfig:
        """
        Loads the active configuration for a tenant.
        """
        # 1. Check Cache (Redis)
        # 2. Fetch from DB
        # SELECT config_json, version FROM tenant_configs WHERE tenant_id = ... AND is_active = TRUE
        # 1. A future optimization can check the Redis cache first.
        # 2. Fetch from DB
        if not self.db:
            from backend.db.client import db_client
            self.db = db_client
        config_data = await self.db.get_tenant_config(tenant_id, version)
        if config_data:
            cjson = config_data['config_json']
            if isinstance(cjson, str):
                cjson = json.loads(cjson)
            cjson = normalize_tenant_config(cjson)
            return TenantConfig(
                tenant_id=tenant_id,
                version=config_data['version'],
                config_data=cjson
            )
        # Fallback / Error
        raise ValueError(f"No active config found for tenant {tenant_id}")
config_loader = ConfigLoader()
