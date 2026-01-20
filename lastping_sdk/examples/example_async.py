import asyncio
from lastping_sdk.async_client import AsyncHeartbeatClient


async def main():
    async with AsyncHeartbeatClient("http://localhost:8000", "MY_API_KEY") as c:
        text = await c.send(1, "async-example")
        print("response:", text)


if __name__ == "__main__":
    asyncio.run(main())
import asyncio
from lastping_sdk.async_client import AsyncHeartbeatClient


async def main():
    async with AsyncHeartbeatClient("http://localhost:8000", "MY_API_KEY") as c:
        text = await c.send(1, "async-example")
        print("response:", text)


if __name__ == "__main__":
    asyncio.run(main())
