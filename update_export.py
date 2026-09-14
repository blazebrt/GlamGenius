import re

content = open('app/domains/privacy/export.py', 'r').read()

# Add import
if 'ScanDecisionEvent' not in content:
    content = content.replace(
        'from app.domains.product.models import ScanEvent, LabelErrorReport',
        'from app.domains.product.models import ScanEvent, LabelErrorReport, ScanDecisionEvent'
    )

# Add to _product_scans
new_logic = '''
    decision_events = await _fetch(
        session,
        select(ScanDecisionEvent)
        .where(ScanDecisionEvent.account_id == account_id)
        .order_by(ScanDecisionEvent.created_at.desc())
    )
'''
if 'ScanDecisionEvent' in new_logic and 'decision_events = await _fetch' not in content:
    content = content.replace(
        'fields = [c.name for c in ScanEvent.__table__.columns]',
        new_logic + '\n    fields = [c.name for c in ScanEvent.__table__.columns]'
    )
    content = content.replace(
        '"label_error_reports": [_row_dict(r, report_fields) for r in reports],',
        '"label_error_reports": [_row_dict(r, report_fields) for r in reports],\n        "scan_decision_events": [_row_dict(r, [c.name for c in ScanDecisionEvent.__table__.columns]) for r in decision_events],'
    )

open('app/domains/privacy/export.py', 'w').write(content)
print("Updated export.py")
