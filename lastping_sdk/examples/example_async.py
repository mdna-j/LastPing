import asyncio
import os

from lastping_sdk.async_client import AsyncHeartbeatClient


async def main():
    base_url = os.environ.get("LASTPING_BASE_URL", "http://localhost:8000")
    api_key = os.environ.get("LASTPING_API_KEY")
    if not api_key:
        raise SystemExit("Set LASTPING_API_KEY in your environment before running this example.")

    async with AsyncHeartbeatClient(base_url, api_key) as c:
        text = await c.send(1, "async-example")
        print("response:", text)


if __name__ == "__main__":
    asyncio.run(main())
