"""India's food grading engine.

European Nutri-Score is unusable here. It flags ghee red because it reads a
cooking fat as a food, and it rates a low-fat biscuit above dal because a
weighted average lets a bad ingredient list buy its way back with a good
number. This engine is a **gate system**: each step can only cap or lower the
grade, never trade one thing off against another.
"""
from app.domains.nutrition.grading.engine import (
    FOOD_GRADE_ENGINE_VERSION,
    GradeResult,
    ProductInput,
    grade_product,
)
from app.domains.nutrition.grading.rules import Grade, GradeOutcome

__all__ = [
    "FOOD_GRADE_ENGINE_VERSION",
    "Grade",
    "GradeOutcome",
    "GradeResult",
    "ProductInput",
    "grade_product",
]
