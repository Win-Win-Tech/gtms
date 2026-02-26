import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gtms.settings")
django.setup()

from datetime import datetime, time, date, timedelta
from django.utils import timezone
from patrol_backend.utils.timezone_utils import get_user_now, combine_date_time_in_user_tz

import pytz
user_tz = pytz.timezone("Asia/Kolkata")
user_now = datetime(2026, 2, 26, 10, 58, 0, tzinfo=user_tz)

print(f"Current Time: {user_now}")
today = user_now.date()
yesterday = today - timedelta(days=1)

# Scenario: Assignment started Feb 25, 19:00 to 07:00. No +1 day on end_date.
shift_start_date = yesterday
start_time = time(19, 0)
end_time = time(7, 0)

shift_start_dt_user = combine_date_time_in_user_tz(shift_start_date, start_time, user_tz)
shift_start_dt_user = shift_start_dt_user.astimezone(user_tz)

if end_time <= start_time:
    shift_end_dt_user = combine_date_time_in_user_tz(shift_start_date + timedelta(days=1), end_time, user_tz)
    shift_end_dt_user = shift_end_dt_user.astimezone(user_tz)
else:
    shift_end_dt_user = combine_date_time_in_user_tz(shift_start_date, end_time, user_tz)
    shift_end_dt_user = shift_end_dt_user.astimezone(user_tz)

print(f"Shift Start DT: {shift_start_dt_user}")
print(f"Shift End DT: {shift_end_dt_user}")
print(f"Is user_now <= shift_end_dt_user ? {user_now <= shift_end_dt_user}")

latest_checkout = shift_end_dt_user + timedelta(minutes=30)
print(f"Latest checkout allowed: {latest_checkout}")
print(f"Is user_now <= latest_checkout ? {user_now <= latest_checkout}")

