import asyncio
import httpx
import time
from uuid import uuid4
# Use the webhook endpoint which bypasses JWT in test simulation usually
# or we mock the signature.
WEBHOOK_URL = "http://localhost:8000/webhook"
async def fire_request(client, i):
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "id": f"wamid.STRESS.{i}.{uuid4()}", # Unique ID
                        "type": "text",
                        "text": {"body": "Stress Test Message"}
                    }]
                }
            }]
        }]
    }
    try:
        resp = await client.post(
            WEBHOOK_URL,
            json=payload,
            headers={"X-Hub-Signature-256": "sha256=mock_bypass"}
        )
        return resp.status_code
    except Exception as e:
        return f"Error: {e}"
async def main():
    concurrency = 50
    print(f"Starting STRESS TEST with {concurrency} concurrent requests...")
    async with httpx.AsyncClient(limits=httpx.Limits(max_keepalive_connections=concurrency)) as client:
        start = time.time()
        tasks = [fire_request(client, i) for i in range(concurrency)]
        results = await asyncio.gather(*tasks)
        duration = time.time() - start
    success = results.count(200)
    print(f"Finished in {duration:.4f}s")
    print(f"Success Rate: {success}/{concurrency}")
    assert success == concurrency, "Stress test failed!"
if __name__ == "__main__":
    asyncio.run(main())