"""MongoDB client and database handle."""
from motor.motor_asyncio import AsyncIOMotorClient
from settings import mongo_url, db_name

client = AsyncIOMotorClient(mongo_url)
db = client[db_name]
