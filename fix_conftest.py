content = open('tests/conftest.py', 'r').read()
old_fixture = '''@pytest.fixture(autouse=True, scope="session")
def mount_today_for_tests():
    try:
        import app.api.v2.today as today_router
        from server import app
        # Only mount if not already mounted
        if not any(r.path == "/api/v2/today" for r in app.routes):
            app.include_router(today_router.router, prefix="/api/v2", tags=["v2-today"])
    except Exception:
        pass'''

new_fixture = '''@pytest.fixture(autouse=True, scope="session")
def mount_legacy_routers_for_tests():
    try:
        import app.api.v2.today as today_router
        import app.api.v2.planner as planner_router
        import app.api.v2.progress as progress_router
        from server import app
        if not any(getattr(r, "path", None) == "/api/v2/today" for r in app.routes):
            app.include_router(today_router.router, prefix="/api/v2", tags=["v2-today"])
        if not any(getattr(r, "path", None) == "/api/v2/planner/events/{event_id}/ready/generate" for r in app.routes):
            app.include_router(planner_router.router, prefix="/api/v2", tags=["v2-planner"])
        if not any(getattr(r, "path", None) == "/api/v2/progress/goals" for r in app.routes):
            app.include_router(progress_router.router, prefix="/api/v2", tags=["v2-progress"])
    except Exception:
        pass'''

if old_fixture in content:
    content = content.replace(old_fixture, new_fixture)
    open('tests/conftest.py', 'w').write(content)
    print("Fixed!")
else:
    print("Fixture not found")
