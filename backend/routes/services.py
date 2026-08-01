"""Salon idea browse routes (no cart/checkout)."""
from fastapi import APIRouter, HTTPException
from typing import Optional

from catalog import SALON_IDEAS

router = APIRouter()

@router.get("/services")
async def get_services(category: Optional[str] = None):
    if category:
        return [s for s in SALON_IDEAS if s["category"].lower() == category.lower()]
    return SALON_IDEAS


@router.get("/services/{service_id}")
async def get_service(service_id: str):
    for service in SALON_IDEAS:
        if service["id"] == service_id:
            return service
    raise HTTPException(status_code=404, detail="Idea not found")


@router.get("/salon-ideas")
async def get_salon_ideas(category: Optional[str] = None):
    return await get_services(category)
