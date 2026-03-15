import asyncio
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


async def main():
    logging.getLogger(__name__).info("claude-bridge starting")


if __name__ == "__main__":
    asyncio.run(main())
