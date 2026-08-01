"""Progressive onboarding routes."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.profile import onboarding, service
from app.domains.profile.schemas import OnboardingStepRequest
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import ValidationFailedError
from app.shared.security.deps import CurrentAccount, get_current_account, require_flag

router = APIRouter(dependencies=[Depends(require_flag("v2_profile"))])


@router.get("/onboarding/status")
async def status(current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    profile = await service.get_or_create_profile(session, current.account_id)
    row = await onboarding.current(session, profile)
    await session.commit()
    return onboarding.serialize(row)


@router.post("/onboarding/step")
async def step(body: OnboardingStepRequest, current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    profile = await service.get_or_create_profile(session, current.account_id)
    row = await onboarding.current(session, profile)
    try:
        await onboarding.record_step(session, profile, row, step=body.step, data=body.data, skipped=body.skipped)
    except ValueError as exc:
        raise ValidationFailedError(str(exc)) from exc
    await session.commit()
    return onboarding.serialize(row)


@router.post("/onboarding/complete")
async def complete(current: CurrentAccount = Depends(get_current_account), session: AsyncSession = Depends(get_session)):
    profile = await service.get_or_create_profile(session, current.account_id)
    row = await onboarding.current(session, profile)
    preview = await onboarding.complete(session, profile, row)
    await session.commit()
    return {**onboarding.serialize(row), "first_result": preview}
