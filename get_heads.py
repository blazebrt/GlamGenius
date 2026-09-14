import os, glob, re
files = glob.glob('migrations/versions/*.py')
revisions = set()
down_revisions = set()
for f in files:
    content = open(f).read()
    rev_match = re.search(r"revision(?:\s*:\s*[^=]+)?\s*=\s*['\"]([^'\"]+)['\"]", content)
    down_match = re.search(r"down_revision(?:\s*:\s*[^=]+)?\s*=\s*['\"]([^'\"]+)['\"]", content)
    
    if rev_match:
        revisions.add(rev_match.group(1))
    if down_match:
        down_revisions.add(down_match.group(1))
        
heads = revisions - down_revisions
print('Heads:', heads)
