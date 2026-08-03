"""Whether two photos may honestly be shown side by side.

The brief's instruction here is the one most worth taking literally:

> If conditions are not comparable, explain why.
> **Do not create fake visual progress.**

Two photos of the same person taken in different light, at a different angle, or
at a different distance will look like a change even when nothing has changed.
Every "before and after" that has ever misled anybody was built out of exactly
that. So this module refuses far more often than it agrees, and when it refuses
it says which condition differed.

The conditions are **recorded by the user when they take the photo**, not
inferred from the image. That is a deliberate choice: guessing lighting from
pixels would be another model whose failures nobody could see, and the user
knows whether they stood by the same window.

The default is refusal. :func:`compare` returns ``comparable=False`` unless
every blocking check passes, so a check that is somehow skipped fails closed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

# The conditions a user records with a progress photo.
LIGHTING = ("daylight_window", "daylight_outdoor", "indoor_warm", "indoor_cool", "mixed", "low_light")
ANGLES = ("front", "left_profile", "right_profile", "three_quarter", "back", "top_down")
FRAMING = ("face_close", "head_and_shoulders", "upper_body", "full_body")
BODY_AREAS = ("face", "hair", "scalp", "skin", "full_body", "hands")
TIMES_OF_DAY = ("morning", "afternoon", "evening", "night")

# Below this, a comparison shows normal day-to-day variation rather than change.
MIN_DAYS_APART = 7
# Above this, too much else has changed — season, haircut, weight, camera — for a
# side-by-side to mean much on its own.
MAX_USEFUL_DAYS_APART = 400

# Lighting groups that are close enough to compare within. Daylight from a
# window and daylight outdoors are not the same, so they are not grouped.
LIGHTING_GROUPS: Dict[str, str] = {
    "daylight_window": "daylight_window",
    "daylight_outdoor": "daylight_outdoor",
    "indoor_warm": "indoor",
    "indoor_cool": "indoor",
    "mixed": "unreliable",
    "low_light": "unreliable",
}

# Minimum pixels on the short edge. Below this, detail differences are the
# camera, not the person.
MIN_DIMENSION = 480


@dataclass
class Check:
    """One comparability condition, and whether it held."""

    key: str
    passed: bool
    blocking: bool
    detail: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key, "passed": self.passed,
            "blocking": self.blocking, "detail": self.detail,
        }


@dataclass
class PhotoConditions:
    """What a photo was taken in. Recorded by the user, not guessed."""

    taken_on: date
    body_area: str
    lighting: str
    angle: str
    framing: str
    time_of_day: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None

    @property
    def short_edge(self) -> Optional[int]:
        if self.width is None or self.height is None:
            return None
        return min(self.width, self.height)


@dataclass
class ComparisonResult:
    comparable: bool
    checks: List[Check] = field(default_factory=list)
    blocking_reasons: List[str] = field(default_factory=list)
    days_apart: Optional[int] = None
    guidance: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "comparable": self.comparable,
            "checks": [row.as_dict() for row in self.checks],
            "blocking_reasons": self.blocking_reasons,
            "days_apart": self.days_apart,
            "guidance": self.guidance,
            "disclaimer": (
                "A side-by-side is only honest when both photos were taken in the same conditions. "
                "We will not show one otherwise."
            ),
        }


def compare(baseline: PhotoConditions, current: PhotoConditions) -> ComparisonResult:
    """Decide whether these two photos may be shown side by side.

    Every check is evaluated — not short-circuited — so the user gets the full
    list of what differed rather than only the first problem.
    """
    checks: List[Check] = []
    days_apart = (current.taken_on - baseline.taken_on).days

    # --- Same subject -------------------------------------------------------
    checks.append(Check(
        key="body_area",
        passed=baseline.body_area == current.body_area,
        blocking=True,
        detail=(
            f"Both photos are of your {current.body_area.replace('_', ' ')}."
            if baseline.body_area == current.body_area
            else f"One photo is of your {baseline.body_area.replace('_', ' ')} and the other is of your "
                 f"{current.body_area.replace('_', ' ')}. Those cannot be compared."
        ),
    ))

    # --- Lighting -----------------------------------------------------------
    baseline_group = LIGHTING_GROUPS.get(baseline.lighting, "unreliable")
    current_group = LIGHTING_GROUPS.get(current.lighting, "unreliable")
    lighting_ok = baseline_group == current_group and baseline_group != "unreliable"
    checks.append(Check(
        key="lighting",
        passed=lighting_ok,
        blocking=True,
        detail=(
            "Both were taken in the same kind of light."
            if lighting_ok
            else "The light is different between the two. Light changes skin and hair more than "
                 "almost anything else, so this alone would make a comparison misleading."
        ),
    ))

    # --- Angle --------------------------------------------------------------
    checks.append(Check(
        key="angle",
        passed=baseline.angle == current.angle,
        blocking=True,
        detail=(
            f"Both taken {baseline.angle.replace('_', ' ')} on."
            if baseline.angle == current.angle
            else f"One is {baseline.angle.replace('_', ' ')} and the other is "
                 f"{current.angle.replace('_', ' ')}. A different angle looks like a different face."
        ),
    ))

    # --- Framing ------------------------------------------------------------
    checks.append(Check(
        key="framing",
        passed=baseline.framing == current.framing,
        blocking=True,
        detail=(
            "Both framed the same way."
            if baseline.framing == current.framing
            else "One is closer than the other. Distance changes apparent size and detail."
        ),
    ))

    # --- Image quality ------------------------------------------------------
    baseline_edge = baseline.short_edge
    current_edge = current.short_edge
    if baseline_edge is None or current_edge is None:
        quality_ok = False
        quality_detail = "We do not have the size of one of these images, so we cannot check quality."
    elif min(baseline_edge, current_edge) < MIN_DIMENSION:
        quality_ok = False
        quality_detail = (
            f"One image is smaller than {MIN_DIMENSION}px on its short edge. Differences at that "
            "size are the camera rather than you."
        )
    else:
        # A large resolution gap makes one photo look sharper, which reads as
        # "better skin" to any human eye.
        ratio = max(baseline_edge, current_edge) / min(baseline_edge, current_edge)
        quality_ok = ratio <= 1.5
        quality_detail = (
            "Both images are a similar quality."
            if quality_ok
            else "One image is much sharper than the other, which on its own looks like a change."
        )
    checks.append(Check(key="image_quality", passed=quality_ok, blocking=True, detail=quality_detail))

    # --- Time difference ----------------------------------------------------
    if days_apart < 0:
        time_ok = False
        time_detail = "The second photo is older than the first."
    elif days_apart < MIN_DAYS_APART:
        time_ok = False
        time_detail = (
            f"Only {days_apart} day(s) apart. Skin and hair vary that much between mornings, "
            f"so nothing meaningful could be read from it. We show comparisons from "
            f"{MIN_DAYS_APART} days apart."
        )
    elif days_apart > MAX_USEFUL_DAYS_APART:
        time_ok = False
        time_detail = (
            f"{days_apart} days apart. Over that long, too much else has changed for a "
            "side-by-side to tell you much."
        )
    else:
        time_ok = True
        time_detail = f"{days_apart} days apart, which is a sensible gap."
    checks.append(Check(key="time_difference", passed=time_ok, blocking=True, detail=time_detail))

    # --- Time of day: worth mentioning, not worth blocking on ---------------
    same_time = (
        baseline.time_of_day is not None
        and baseline.time_of_day == current.time_of_day
    )
    checks.append(Check(
        key="time_of_day",
        passed=same_time,
        blocking=False,
        detail=(
            "Both taken at a similar time of day."
            if same_time
            else "Taken at different times of day, which changes light a little. Not enough to "
                 "stop the comparison, but worth knowing."
        ),
    ))

    blocking_failures = [row for row in checks if row.blocking and not row.passed]
    result = ComparisonResult(
        comparable=not blocking_failures,
        checks=checks,
        blocking_reasons=[row.detail for row in blocking_failures],
        days_apart=days_apart,
    )
    if blocking_failures:
        result.guidance = guidance_for(blocking_failures)
    return result


def guidance_for(failures: Sequence[Check]) -> List[str]:
    """How to take a photo that will compare properly next time.

    Specific and actionable. "Conditions not comparable" on its own tells a
    person nothing they can do differently.
    """
    tips: List[str] = []
    keys = {row.key for row in failures}
    if "lighting" in keys:
        tips.append("Stand in the same spot by the same window, and avoid overhead or coloured light.")
    if "angle" in keys:
        tips.append("Face the camera the same way each time — straight on is easiest to repeat.")
    if "framing" in keys:
        tips.append("Hold the camera the same distance away. Arm's length, at eye level, works.")
    if "image_quality" in keys:
        tips.append("Use the same camera, and make sure the photo is in focus.")
    if "time_difference" in keys:
        tips.append(f"Leave at least {MIN_DAYS_APART} days between photos you want to compare.")
    if "body_area" in keys:
        tips.append("Compare photos of the same area.")
    return tips


def pick_baseline(
    candidates: Sequence[Any], current: PhotoConditions
) -> Tuple[Optional[Any], List[ComparisonResult]]:
    """The best comparable earlier photo, and why the others were rejected.

    Prefers the oldest comparable photo, because the longest honest gap shows
    the most. Returns every evaluation so the app can explain a refusal instead
    of silently showing nothing.
    """
    evaluations: List[ComparisonResult] = []
    comparable: List[Tuple[Any, ComparisonResult]] = []
    for row in candidates:
        conditions = PhotoConditions(
            taken_on=row.taken_on, body_area=row.body_area, lighting=row.lighting,
            angle=row.angle, framing=row.framing, time_of_day=row.time_of_day,
            width=getattr(row, "width", None), height=getattr(row, "height", None),
        )
        result = compare(conditions, current)
        evaluations.append(result)
        if result.comparable:
            comparable.append((row, result))

    if not comparable:
        return None, evaluations
    best = max(comparable, key=lambda pair: pair[1].days_apart or 0)
    return best[0], evaluations
