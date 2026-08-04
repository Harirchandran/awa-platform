"""
Quick test for Groq API with llama-3.3-70b-versatile
"""
import asyncio
import sys
from pathlib import Path
import pytest
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
# Load env vars
from dotenv import load_dotenv
import os
load_dotenv(PROJECT_ROOT / "backend" / ".env.local")
if os.getenv("AWA_INTEGRATION_MODE", "mock") == "mock":
    os.environ.pop("GROQ_API_KEY", None)
from backend.ai.providers.groq_provider import GroqProvider
pytestmark = pytest.mark.anyio
def _live_smoke_enabled() -> bool:
    return os.getenv("RUN_LIVE_PROVIDER_SMOKE") == "1"

@pytest.mark.skipif(
    not _live_smoke_enabled(),
    reason="Set RUN_LIVE_PROVIDER_SMOKE=1 with live provider credentials to run.",
)
async def test_groq():
    print("=" * 60)
    print("Testing Groq API with llama-3.3-70b-versatile")
    print("=" * 60)
    provider = GroqProvider()
    if not provider.api_key:
        pytest.skip("GROQ_API_KEY is not configured in this environment")
    print(f"Provider: {provider.get_provider_name()}")
    print(f"Model: {provider.MODEL}")
    print(f"API Key: {provider.api_key[:20]}..." if provider.api_key else "NOT SET")
    print()
    # Test 1: Simple greeting
    print("Test 1: Simple greeting")
    response = await provider.generate_response(
        system_prompt="You are a helpful assistant for a salon booking system.",
        user_message="Hello! What services do you offer?"
    )
    print(f"Response: {response.content[:200]}...")
    print()
    # Test 2: Booking intent
    print("Test 2: Booking intent understanding")
    response = await provider.generate_response(
        system_prompt="You are a WhatsApp bot for 'Glamour Salon'. Services: Haircut ($30), Manicure ($25), Facial ($50). Help customers book appointments.",
        user_message="I want to book a haircut for tomorrow at 3pm"
    )
    print(f"Response: {response.content[:300]}...")
    print()
    # Test 3: Data collection
    print("Test 3: Collecting customer name")
    response = await provider.generate_response(
        system_prompt="You are collecting booking details. You need: full name, service, date, time. Be concise and friendly.",
        user_message="My name is John Smith"
    )
    print(f"Response: {response.content[:200]}...")
    print()
    print("=" * 60)
    print("Groq API test completed!")
    print("=" * 60)
if __name__ == "__main__":
    asyncio.run(test_groq())
