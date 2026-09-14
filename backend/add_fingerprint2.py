import re

content = open('../frontend/src/services/verdictModel.ts', 'r').read()

content = content.replace(
    "labelVersion?: { id: string; versionNumber: number; observedAt: string; changedFields: string[]; completeness: string } | null;",
    "labelVersion?: { id: string; versionNumber: number; contentFingerprint: string; observedAt: string; changedFields: string[]; completeness: string } | null;"
)

open('../frontend/src/services/verdictModel.ts', 'w').write(content)
print("Updated verdictModel.ts")
