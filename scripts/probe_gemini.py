#!/usr/bin/env python3
"""Live probe for Gemini API using actual backend AI gateway and production schemas.

This script tests the integration with the AI Gateway and validates the production
schemas for scan analysis, occasion styling, shopping evaluation, and routine assistance.
"""
import os
import sys
import asyncio
from unittest.mock import patch
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../backend")))
load_dotenv(os.path.join(os.path.dirname(__file__), "../backend/.env"))

from app.domains.ai_gateway.gateway import run_structured
from app.domains.ai_gateway.schemas import CoachAnalysis, SCHEMA_VERSION_COACH
from app.domains.recommendation.schemas import LookExplanationResponse, SCHEMA_VERSION_LOOK_EXPLANATION, PurchaseNarrative, SCHEMA_VERSION_PURCHASE_EXPLANATION
from app.domains.routines.schemas import RoutineExplanationResponse, SCHEMA_VERSION_ROUTINE_EXPLANATION
from app.shared.errors.exceptions import AnalysisUnavailableError
from app.shared.errors.codes import AIFailureType

async def test_schema(name, feature, prompt, system, schema, prompt_version, schema_version):
    print(f"Testing schema: {name}")
    try:
        # Mock _record_run to avoid needing a real database connection for the probe
        with patch("app.domains.ai_gateway.gateway._record_run", return_value=None):
            result = await run_structured(
                feature=feature,
                prompt=prompt,
                system=system,
                schema=schema,
                prompt_version=prompt_version,
                schema_version=schema_version,
            )
        print(f"  [OK] Successfully parsed result: {result}")
        return True
    except AnalysisUnavailableError as e:
        if e.failure_type == AIFailureType.PROVIDER_NOT_CONFIGURED:
            print(f"  [SKIP] Provider not configured (Missing GEMINI_API_KEY). {e.message}")
            return False
        else:
            print(f"  [ERROR] Analysis failed: {e.failure_type} - {e}")
            return False
    except Exception as e:
        print(f"  [ERROR] Unexpected error: {e}")
        return False

async def main():
    print("Starting AI Gateway Gemini probe...")
    
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("WARNING: GEMINI_API_KEY is not set. The tests will verify the gateway rejects calls safely.")
    
    # 1. Scan Analysis
    await test_schema(
        name="Scan Analysis (CoachAnalysis)",
        feature="scan_analysis",
        prompt="Describe a simple look.",
        system="You are a coach.",
        schema=CoachAnalysis,
        prompt_version="test-v1",
        schema_version=SCHEMA_VERSION_COACH
    )

    # 2. Occasion Styling
    await test_schema(
        name="Occasion Styling (LookExplanationResponse)",
        feature="occasion_styling",
        prompt="Explain why this outfit works.",
        system="You are a stylist.",
        schema=LookExplanationResponse,
        prompt_version="test-v1",
        schema_version=SCHEMA_VERSION_LOOK_EXPLANATION
    )

    # 3. Shopping Evaluation
    await test_schema(
        name="Shopping Evaluation (PurchaseNarrative)",
        feature="shopping_evaluation",
        prompt="Explain this purchase.",
        system="You evaluate purchases.",
        schema=PurchaseNarrative,
        prompt_version="test-v1",
        schema_version=SCHEMA_VERSION_PURCHASE_EXPLANATION
    )

    # 4. Routine Assistance
    await test_schema(
        name="Routine Assistance (RoutineExplanationResponse)",
        feature="routine_assistance",
        prompt="Explain this routine.",
        system="You explain routines.",
        schema=RoutineExplanationResponse,
        prompt_version="test-v1",
        schema_version=SCHEMA_VERSION_ROUTINE_EXPLANATION
    )
    
    print("Probe finished.")
    if not api_key:
        print("Note: Since GEMINI_API_KEY was missing, full live validation was skipped.")

if __name__ == "__main__":
    asyncio.run(main())
