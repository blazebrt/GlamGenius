#!/usr/bin/env python3
"""Live probe for Gemini API."""
import os
import sys
import asyncio
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))
load_dotenv(os.path.join(os.path.dirname(__file__), "../backend/.env"))

from app.domains.ai_gateway.client import AIGatewayClient
from app.config import GEMINI_MODEL

async def main():
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY or GOOGLE_API_KEY is not set.")
        sys.exit(1)

    print(f"Testing Gemini API connectivity using model: {GEMINI_MODEL}")
    
    try:
        client = AIGatewayClient()
        # Create a simple test generation
        # We don't have to save it to DB, just hit the API
        response = await client.generate_content_async(
            "Say 'Hello, World!'",
            model=GEMINI_MODEL
        )
        print("Success! Response from Gemini:")
        print(response.text)
        sys.exit(0)
    except Exception as e:
        print(f"Failed to connect to Gemini API: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
