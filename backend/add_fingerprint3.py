import re

content = open('../frontend/src/services/verdictClient.ts', 'r').read()

content = content.replace(
    'versionNumber: wire.label_version.version_number,',
    'versionNumber: wire.label_version.version_number, contentFingerprint: wire.label_version.content_fingerprint,'
)

open('../frontend/src/services/verdictClient.ts', 'w').write(content)
print("Updated verdictClient.ts")
