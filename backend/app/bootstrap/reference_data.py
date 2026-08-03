"""CLI: ``python -m app.bootstrap.reference_data``."""
from app.bootstrap import main
import asyncio

asyncio.run(main())
