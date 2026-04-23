import os, django, sys
sys.path.insert(0, '/var/www/html/babu/c/czip/GTMS/backendnew/gtms')
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "patrol_backend.settings")
django.setup()

from checkin.models import CheckIn
from scheduler.models import Assignment, Checkpoint
import pytz
from datetime import datetime, timedelta, time as dtime
from collections import defaultdict
from patrol_backend.utils.timezone_utils import combine_date_time_in_user_tz
from django.db.models import Q

user_tz = pytz.timezone('Asia/Kolkata')
location_id = '28ef96dc-dab3-4910-8777-d93a864cf017'
filter_type = 'custom'
start_date_str = '2026-04-01'
end_date_str = '2026-04-22'

today = datetime.now(user_tz).date()
start_dt_user = datetime.combine(datetime.strptime(start_date_str, "%Y-%m-%d").date(), datetime.min.time())
end_dt_user = datetime.combine(datetime.strptime(end_date_str, "%Y-%m-%d").date(), datetime.max.time())
end_date = min(end_dt_user.date(), today)

query_start_date = start_dt_user.date() - timedelta(days=1)
query_end_date = end_dt_user.date()
assignments = Assignment.objects.filter(
    start_date__lte=query_end_date,
    end_date__gte=query_start_date,
    location_id=location_id
).select_related('guard', 'location', 'shift')

overall_start_date = start_dt_user.date() - timedelta(days=1)
overall_end_date = end_dt_user.date() + timedelta(days=1)
global_start_utc = combine_date_time_in_user_tz(overall_start_date, datetime.min.time(), user_tz).astimezone(pytz.UTC)
global_end_utc   = combine_date_time_in_user_tz(overall_end_date, datetime.max.time(), user_tz).astimezone(pytz.UTC)

guard_ids = [a.guard_id for a in assignments]
print(f"guard_ids count: {len(guard_ids)}")
print(f"global_start_utc: {global_start_utc}")
print(f"global_end_utc:   {global_end_utc}")

# Check if the target guard is in guard_ids
target_guard = '5561b9d9-8d7a-41ee-b610-ee6ee2e30acc'
import uuid
print(f"Target guard in guard_ids: {uuid.UUID(target_guard) in guard_ids}")

# Check if the target checkin is in all_checkins
all_checkins = list(CheckIn.objects.filter(
    guard_id__in=guard_ids,
    timestamp__gte=global_start_utc,
    timestamp__lt=global_end_utc
).order_by('timestamp'))
print(f"Total checkins fetched: {len(all_checkins)}")

target_checkin = '74f6a410-edf5-4546-ae65-2ff3843bcb33'
found = any(str(c.id) == target_checkin for c in all_checkins)
print(f"Target checkin in bulk fetch: {found}")

# Build map and check
checkin_map = defaultdict(list)
for c in all_checkins:
    key = (str(c.guard_id), str(c.shift_id), str(c.checkpoint_id))
    checkin_map[key].append(c)

expected_key = ('5561b9d9-8d7a-41ee-b610-ee6ee2e30acc', '3320f26e-f8a1-42a2-b4e7-64057348fdc1', 'bb036081-3539-4526-bf05-df26976e4194')
print(f"Target key has {len(checkin_map.get(expected_key, []))} entries in map")
