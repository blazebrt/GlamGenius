"""The B2B API, mounted at ``/api/b2b/v1``. One read-only route.

This is a separate surface from the consumer API (``/api/v2``) with a separate
security principal: a B2B API key, presented as ``Authorization: Bearer
ggb_…``. The consumer API never accepts one, and this API never accepts a
consumer Supabase token, a device token or an account.

``GET /api/b2b/v1/products/{barcode}/truth`` — current verified Product Truth
for one exact GS1 barcode, from GlamGenius's own confirmed labels and
published rules. See ``docs/api/B2B_PRODUCT_TRUTH_API_V1.md`` for the
integrator guide and ``docs/architecture/B2B_PRODUCT_TRUTH_API.md`` for the
design.

Order of work for a request, cheapest refusal first, and nothing expensive
before every gate has passed:

1. malformed credential refused before SQL;
2. trusted-network admission for valid-shaped credentials;
3. credential (one indexed lookup by public prefix; one generic 401 for every
   authentication failure);
4. per-minute burst limit for that client (in memory);
5. request shape — no query parameters, an exact GS1 barcode (422);
6. daily allowance, one atomic statement, committed at once (429);
7. Product Truth, from Store B and published rules;
8. usage outcome counted, committed.

Logs name the client key, the key prefix, the outcome and the request id.
Never the credential, its hash, the Authorization header, the barcode, the
label or the answer.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.b2b.schemas import ErrorResponse, ProductTruthResponse
from app.domains.b2b import access, quota
from app.domains.b2b import truth as b2b_truth
from app.domains.b2b.credentials import prefix_of
from app.shared.database.base import utcnow
from app.shared.database.sql import get_session
from app.shared.observability.request_id import get_request_id
from app.shared.security.network import client_ip

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/b2b/v1", tags=["b2b-product-truth-v1"])

#: Its own OpenAPI security scheme, so the B2B credential is never confused
#: with the consumer bearer token in generated clients or documentation.
_b2b_key = HTTPBearer(
    auto_error=False,
    scheme_name="GlamGeniusB2BApiKey",
    bearerFormat="ggb_<prefix>_<secret>",
    description="A GlamGenius B2B API key issued by GlamGenius. Not a consumer token.",
)


def _error(status_code: int, code: str, message: str, headers: dict[str, str] | None = None) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, "request_id": get_request_id()},
        headers=headers,
    )


def _unauthenticated() -> HTTPException:
    # One body for absent, malformed, unknown, wrong, expired, revoked and
    # suspended. Nothing in it distinguishes them.
    return _error(
        status.HTTP_401_UNAUTHORIZED, "B2B_UNAUTHENTICATED", "A valid GlamGenius B2B API key is required.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _rate_limited(code: str, retry_after: int) -> HTTPException:
    return _error(
        status.HTTP_429_TOO_MANY_REQUESTS, code, "Request limit reached. Retry after the stated interval.",
        headers={"Retry-After": str(retry_after)},
    )


async def b2b_caller(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_b2b_key),
    session: AsyncSession = Depends(get_session),
) -> access.B2BCaller:
    """Bound network lookups, authenticate, then apply the client's burst limit."""
    presented = credentials.credentials if credentials is not None else None
    if prefix_of(presented) is None:
        raise _unauthenticated()
    retry_after = quota.auth_network_retry_after(client_ip(request))
    if retry_after is not None:
        raise _rate_limited("B2B_RATE_LIMITED", retry_after)
    caller = await access.authenticate(session, presented)
    if caller is None:
        # The prefix only when the value had the credential's exact shape: it
        # is public, and it is the one thing an operator can trace.
        logger.info("b2b_request_refused outcome=unauthenticated key_prefix=%s", prefix_of(presented) or "-")
        raise _unauthenticated()
    retry_after = quota.burst_retry_after(caller.client_id, caller.requests_per_minute)
    if retry_after is not None:
        await quota.record_rate_limited(session, caller.client_id)
        await session.commit()
        logger.info(
            "b2b_request_refused outcome=rate_limited limit=burst client_key=%s key_prefix=%s",
            caller.client_key, caller.key_prefix,
        )
        raise _rate_limited("B2B_RATE_LIMITED", retry_after)
    return caller


_ERRORS = {
    status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse, "description": "Missing or invalid B2B API key."},
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "model": ErrorResponse, "description": "Not an exact GS1 barcode, or a query parameter was sent.",
    },
    status.HTTP_429_TOO_MANY_REQUESTS: {
        "model": ErrorResponse, "description": "Per-minute or daily limit reached. See Retry-After.",
    },
}


@router.get(
    "/products/{barcode}/truth",
    response_model=ProductTruthResponse,
    responses=_ERRORS,
    summary="Verified Product Truth for one exact barcode",
    description=(
        "GlamGenius's current Buy / Wait / Skip decision, grade, factors and published evidence for one "
        "exact GS1 barcode, computed from GlamGenius's own confirmed pack labels and published rules. "
        "Returns state `not_enough_information` (HTTP 200) when that verified-data requirement is not met. "
        "Read-only. Accepts no query parameters."
    ),
    operation_id="readB2BProductTruth",
)
async def read_product_truth(
    request: Request,
    barcode: str = Path(
        ..., description="An exact GS1 GTIN-8, -12, -13 or -14 with a valid check digit.",
        examples=["8901000000001"],
    ),
    caller: access.B2BCaller = Depends(b2b_caller),
    session: AsyncSession = Depends(get_session),
) -> dict:
    # Nothing a caller adds can steer the answer, so nothing is accepted:
    # a desired verdict, a tone, a policy or a profile is refused, not ignored.
    if request.query_params:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "B2B_QUERY_NOT_ACCEPTED",
                     "This endpoint accepts no query parameters.")
    if not b2b_truth.is_exact_gtin(barcode):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "B2B_BARCODE_INVALID",
                     "The barcode must be an exact GS1 GTIN-8, -12, -13 or -14 with a valid check digit.")
    usage_date = await quota.acquire_daily(session, caller.client_id)
    await session.commit()
    if usage_date is None:
        await quota.record_rate_limited(session, caller.client_id)
        retry_after = await quota.seconds_until_daily_reset(session)
        await session.commit()
        logger.info(
            "b2b_request_refused outcome=rate_limited limit=daily client_key=%s key_prefix=%s",
            caller.client_key, caller.key_prefix,
        )
        raise _rate_limited("B2B_DAILY_QUOTA_EXHAUSTED", retry_after)
    body = await b2b_truth.product_truth(session, barcode)
    await quota.record_outcome(
        session, caller.client_id, usage_date, available=body["state"] == b2b_truth.STATE_AVAILABLE,
    )
    await session.commit()
    logger.info(
        "b2b_request_served outcome=%s client_key=%s key_prefix=%s",
        body["state"], caller.client_key, caller.key_prefix,
    )
    return {**body, "meta": {"request_id": get_request_id(), "generated_at": utcnow()}}


__all__ = ["router"]
