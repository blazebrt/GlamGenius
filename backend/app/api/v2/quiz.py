"""Quiz API: GET questions, POST submission, GET latest / history."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.quiz.models import QuizSubmission
from app.domains.quiz.questions import (
    QUESTIONS,
    QUIZ_SCHEMA_VERSION,
    all_question_ids,
)
from app.shared.database.sql import get_session
from app.shared.security.deps import (
    CurrentAccount,
    get_current_account,
    require_flag,
)

router = APIRouter(dependencies=[Depends(require_flag("v2_quiz"))])


class QuizSubmitRequest(BaseModel):
    # {"vibe": "polished", "occasion_focus": "work", ...}
    answers: dict[str, str]


def _derive_style_vibe(answers: dict[str, str]) -> str:
    """Deterministic derivation from the answers.

    Auditable: given the same answers, the same vibe is chosen. No hidden AI.
    """
    return (answers.get("vibe") or "").strip() or "natural"


def _serialise(sub: QuizSubmission) -> dict[str, Any]:
    return {
        "id": str(sub.id),
        "schema_version": sub.schema_version,
        "answers": sub.answers,
        "derived_style_vibe": sub.derived_style_vibe,
        "submitted_at": sub.created_at.isoformat() if sub.created_at else None,
    }


@router.get("/quiz/questions")
async def get_questions():
    return {
        "schema_version": QUIZ_SCHEMA_VERSION,
        "questions": QUESTIONS,
    }


@router.post("/quiz/submit", status_code=status.HTTP_201_CREATED)
async def submit_quiz(
    body: QuizSubmitRequest,
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    required = set(all_question_ids())
    provided = set(body.answers.keys())
    missing = sorted(required - provided)
    unexpected = sorted(provided - required)
    if missing or unexpected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "quiz_answers_invalid",
                "missing": missing,
                "unexpected": unexpected,
                "message": (
                    "Please answer every question. If you saw this after "
                    "answering, the quiz may have been updated — refresh and "
                    "try again."
                ),
            },
        )

    submission = QuizSubmission(
        account_id=current.account_id,
        schema_version=QUIZ_SCHEMA_VERSION,
        answers=body.answers,
        derived_style_vibe=_derive_style_vibe(body.answers),
    )
    session.add(submission)
    await session.commit()
    await session.refresh(submission)
    return _serialise(submission)


@router.get("/quiz/latest")
async def latest_submission(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
):
    row = (
        await session.execute(
            select(QuizSubmission)
            .where(QuizSubmission.account_id == current.account_id)
            .order_by(QuizSubmission.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return {"submission": None}
    return {"submission": _serialise(row)}


@router.get("/quiz/history")
async def submission_history(
    current: CurrentAccount = Depends(get_current_account),
    session: AsyncSession = Depends(get_session),
    limit: int = 20,
):
    rows: list[QuizSubmission] = list(
        (
            await session.execute(
                select(QuizSubmission)
                .where(QuizSubmission.account_id == current.account_id)
                .order_by(QuizSubmission.created_at.desc())
                .limit(min(max(limit, 1), 100))
            )
        ).scalars().all()
    )
    return {"submissions": [_serialise(r) for r in rows]}
