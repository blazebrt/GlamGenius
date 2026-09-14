content = open('app/domains/privacy/__init__.py', 'r').read()
if '"scan_decision_events": Classification.INCLUDED' not in content:
    content = content.replace(
        '"scan_events": Classification.INCLUDED,',
        '"scan_events": Classification.INCLUDED,\n    "scan_decision_events": Classification.INCLUDED,'
    )
    open('app/domains/privacy/__init__.py', 'w').write(content)
    print("Updated __init__.py")
