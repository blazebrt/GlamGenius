"""Notification settings and device registration — the production authority.

These four routes are the only way the app reads and changes notification
preferences, and registers or removes this installation's push device. The
Notifications screen uses them, and so does logout, which removes this
installation's device for the account that is signing out.

The paths keep their ``/today/`` segment so the installed app keeps working.
That segment is compatibility syntax and nothing more: the retired Today
product is not mounted in production (``app.api.v2`` does not include
``today.router``), and nothing here depends on it or on its ``v2_today``
feature flag. Notification settings stay available however that flag is set.

Every route is signed-in and account-scoped. The device routes act on the
authenticated account's own row for one exact ``device_key``, so a request from
one account can never remove or register a device row that belongs to another.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.planning import clock, notifications
from app.domains.planning import context as context_stage
from app.domains.planning.schemas import NotificationDeviceRegister, NotificationPreferencePatch
from app.domains.product import watch as product_watch
from app.shared.database.sql import get_session
from app.shared.errors.exceptions import ValidationFailedError
from app.shared.security.deps import CurrentAccount, get_current_account

router = APIRouter()

#: The four retained paths, relative to ``/api/v2``. A test holds the
#: production router to exactly these, and holds every retired router to none
#: of them, so they cannot quietly move back into an unmounted module.
NOTIFICATION_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/today/notifications"),
    ("PATCH", "/today/notifications"),
    ("POST", "/today/notifications/devices"),
    ("DELETE", "/today/notifications/devices/{device_key}"),
)


@router.get("/today/notifications")
async def get_notification_preferences(
    device_key: str | None = Query(None, max_length=160),
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    timezone_name = await context_stage.resolve_timezone_for(session, current.account_id)
    row = await notifications.preferences_for(session, current.account_id, timezone_name)
    await session.commit()
    return {
        "preferences": notifications.serialize_preferences(row),
        "recent": await notifications.recent_deliveries(session, current.account_id),
        "current_device_registered": (
            await notifications.current_device_registered(session, current.account_id, device_key)
            if device_key else False
        ),
    }


@router.post("/today/notifications/devices")
async def register_notification_device(
    body: NotificationDeviceRegister,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    device = await notifications.register_device(
        session, current.account_id, device_key=body.device_key,
        platform=body.platform, expo_push_token=body.expo_push_token,
    )
    preference = await notifications.preferences_for(session, current.account_id, clock.DEFAULT_TIMEZONE)
    preference.native_push_enabled = True
    await session.commit()
    return {
        "device": {"device_key": device.device_key, "platform": device.platform, "status": device.status},
        "native_push_enabled": True,
        "current_device_registered": True,
    }


@router.delete("/today/notifications/devices/{device_key}")
async def unregister_notification_device(
    device_key: str,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    removed = await notifications.unregister_device(session, current.account_id, device_key)
    if not removed:
        raise HTTPException(status_code=404, detail={"code": "device_not_found", "message": "That device is not registered."})
    active_remaining = bool(await notifications.active_devices(session, current.account_id))
    preference = await notifications.preferences_for(session, current.account_id, clock.DEFAULT_TIMEZONE)
    if not active_remaining:
        preference.native_push_enabled = False
    await session.commit()
    return {
        "device_key": device_key,
        "removed": True,
        "active_devices_remaining": active_remaining,
        "native_push_enabled": bool(preference.native_push_enabled),
        "current_device_registered": False,
    }


@router.patch("/today/notifications")
async def patch_notification_preferences(
    body: NotificationPreferencePatch,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    timezone_name = await context_stage.resolve_timezone_for(session, current.account_id)
    # Locked, and before any watch row: the notification worker takes the same
    # two locks in the same order, so turning Product Watch back on cannot
    # deadlock with a cycle that is deciding about it.
    row = await notifications.preferences_for(session, current.account_id, timezone_name, lock=True)
    was_listening = product_watch.listening(row)
    fields = body.model_dump(exclude_unset=True)
    if fields.get("native_push_enabled") is True and not await notifications.active_devices(session, current.account_id):
        raise ValidationFailedError("Register this device before enabling native notifications.", field="native_push_enabled")
    if "modules" in fields and fields["modules"] is not None:
        row.modules = {**(row.modules or {}), **fields.pop("modules")}
    if "topics" in fields and fields["topics"] is not None:
        row.topics = {**(row.topics or {}), **fields.pop("topics")}
    for key, value in fields.items():
        if value is not None:
            setattr(row, key, value)
    if not was_listening and product_watch.listening(row):
        # Nothing was evaluated for this account's watched products while it
        # had asked not to hear about them. Whatever became true meanwhile is
        # baseline now, so switching back on does not deliver a backlog.
        await product_watch.rebaseline_account(session, current.account_id)
    await session.commit()
    return {"preferences": notifications.serialize_preferences(row)}
