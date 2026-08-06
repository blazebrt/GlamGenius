import os
for root, dirs, files in os.walk('backend'):
    if 'node_modules' in root or '.git' in root or '.venv' in root or '__pycache__' in root:
        continue
    for f in files:
        if not f.endswith(('.md', '.py', '.yml', '.yaml', '.sh', '.txt')):
            continue
        filepath = os.path.join(root, f)
        try:
            with open(filepath, 'r', encoding='utf-8') as file:
                lines = file.readlines()
                for i, line in enumerate(lines):
                    if 'SUPABASE_JWT_SECRET' in line or 'HS256' in line:
                        print(f'{filepath}:{i+1}:{line.strip()}')
        except Exception as e:
            pass
