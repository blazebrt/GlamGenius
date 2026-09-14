import re

content = open('app/api/v2/product.py', 'r').read()

content = content.replace(
    '"version_number": snapshot.version_number,',
    '"version_number": snapshot.version_number,\n        "content_fingerprint": snapshot.content_fingerprint,'
)

open('app/api/v2/product.py', 'w').write(content)
print("Updated product.py with content_fingerprint")
