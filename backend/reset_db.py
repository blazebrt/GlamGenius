import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def main():
    engine = create_async_engine('postgresql+asyncpg://glamgenius:glamgenius@postgres:5432/glamgenius_test')
    async with engine.begin() as conn:
        await conn.execute(text('DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO postgres; GRANT ALL ON SCHEMA public TO public;'))
    print('DB dropped')

if __name__ == '__main__':
    asyncio.run(main())
