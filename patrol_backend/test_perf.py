import os
import django
import sys
import time

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "patrol_backend.settings")
django.setup()

from dashboard.views import _get_checkin_report_data_v2

print("Starting v2 test...")
start = time.time()
try:
    report = _get_checkin_report_data_v2(
        filter_type='custom',
        start_date_str='2026-04-01',
        end_date_str='2026-04-22',
        location_id='28ef96dc-dab3-4910-8777-d93a864cf017'
    )
    print(f"Finished v2! Got {len(report)} items.")
except Exception as e:
    import traceback
    traceback.print_exc()
print(f"Time taken V2: {time.time() - start:.2f}s")
