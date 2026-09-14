import sys
import os
sys.path.append(os.getcwd())
try:
    from server import app
    print(any(getattr(r, 'path', '') == '/api/v2/today' for r in app.routes))
except Exception as e:
    print('Error:', e)
