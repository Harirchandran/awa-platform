import pytest
from backend.schemas.config import OrderModuleConfig

def test_order_module_config_defaults():
    config = OrderModuleConfig(
        enabled=True,
        fields=[]
    )
    assert config.delivery_fee == 0.0
    assert config.tax_amount == 0.0

def test_order_module_config_custom_values():
    config = OrderModuleConfig(
        enabled=True,
        fields=[],
        delivery_fee=15.50,
        tax_amount=5.00
    )
    assert config.delivery_fee == 15.50
    assert config.tax_amount == 5.00
