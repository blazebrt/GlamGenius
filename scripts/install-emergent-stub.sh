#!/usr/bin/env bash
# Local dev stub for emergentintegrations (not on public PyPI).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STUB="$ROOT/.dev/emergentintegrations-stub"
mkdir -p "$STUB/emergentintegrations/llm"

if [[ -f "$STUB/pyproject.toml" ]]; then
  exit 0
fi

cat > "$STUB/pyproject.toml" <<'EOF'
[project]
name = "emergentintegrations"
version = "0.1.0"
description = "Local dev stub for GlamGenius backend"
requires-python = ">=3.10"

[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["."]
EOF

touch "$STUB/emergentintegrations/__init__.py" "$STUB/emergentintegrations/llm/__init__.py"

cat > "$STUB/emergentintegrations/llm/chat.py" <<'EOF'
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List
import json


@dataclass
class FileContentWithMimeType:
    file_path: str
    mime_type: str


@dataclass
class UserMessage:
    text: str = ""
    file_contents: List[FileContentWithMimeType] = field(default_factory=list)


class LlmChat:
    def __init__(self, api_key: str = "", session_id: str = "", system_message: str = ""):
        self.api_key = api_key
        self.session_id = session_id
        self.system_message = system_message

    def with_model(self, provider: str, model: str) -> "LlmChat":
        return self

    async def send_message(self, user_message: UserMessage) -> str:
        if user_message.file_contents:
            return json.dumps(
                {
                    "face_shape": "oval",
                    "skin_type": "combination",
                    "overall_score": 72,
                    "health_scores": {"overall_skin_health": 72},
                    "skin_concerns": [],
                    "recommended_treatments": [],
                    "confidence_score": 0.75,
                }
            )
        return json.dumps(
            {
                "services": [
                    {
                        "name": "Hydrating Facial Treatment",
                        "reason": "Dev stub recommendation",
                        "expected_result": "Glowing skin",
                        "price": 999,
                    }
                ],
                "stylist_level": "Senior Stylist",
                "add_ons": [],
                "expected_outcome": "Refreshed look",
                "aftercare_tips": ["Stay hydrated"],
                "maintenance_tips": ["Use sunscreen"],
                "upsell_suggestions": [],
                "total_estimated_cost": 999,
                "total_duration": 60,
                "appointment_duration": "60 minutes",
            }
        )
EOF

echo "Created emergentintegrations dev stub at $STUB"
