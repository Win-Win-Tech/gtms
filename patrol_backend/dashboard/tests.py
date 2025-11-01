from django.test import TestCase

# Create your tests here.
from datetime import datetime, timedelta
from io import BytesIO
import os

from django.utils.timezone import make_aware, is_aware, now
from django.conf import settings
from openpyxl import Workbook
#from django.contrib.sites.models import Site
from .models import AttendanceCheckin, CheckInLog
from scheduler.models import Checkpoint, Assignment
from checkin.models import CheckIn

def generate_checkin_report(filter_type='today', start_date=None, end_date=None, user_id=None, location_id=None):
    today = now().date()

    # Determine date range
    if filter_type == 'today':
        start = datetime.combine(today, datetime.min.time())
        end = datetime.combine(today, datetime.max.time())
    elif filter_type == 'this_week':
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        start = datetime.combine(start, datetime.min.time())
        end = datetime.combine(end, datetime.max.time())
    elif filter_type == 'this_month':
        start = datetime(today.year, today.month, 1)
        next_month = start.replace(day=28) + timedelta(days=4)
        end = datetime(next_month.year, next_month.month, 1) - timedelta(seconds=1)
    elif filter_type == 'custom' and start_date and end_date:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
    else:
        raise ValueError("Invalid filter or missing dates")

    # Query assignments
    assignments = Assignment.objects.filter(
        start_date__lte=end.date(),
        end_date__gte=start.date()
    )
    if user_id:
        assignments = assignments.filter(guard_id=user_id)
    if location_id:
        assignments = assignments.filter(location_id=location_id)

    # Create Excel workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Check-In Report"

    headers = [
        'Guard ID', 'Guard Name', 'Checkpoint ID', 'Checkpoint Name',
        'Expected Time', 'Actual Check-In Time', 'Status', 'Delay (minutes)'
    ]
    ws.append(headers)

    for assignment in assignments:
        guard = assignment.guard
        shift = assignment.shift
        for cp in assignment.checkpoints:
            checkpoint_id = cp.get('checkpoint_id')
            expected_time_str = cp.get('time')
            expected_time = datetime.combine(assignment.start_date, datetime.strptime(expected_time_str, "%H:%M").time())
            expected_time = make_aware(expected_time)

            checkin = CheckIn.objects.filter(
                guard=guard,
                shift=shift,
                checkpoint_id=checkpoint_id,
                timestamp__range=(start, end)
            ).order_by('timestamp').first()

            actual_time = checkin.timestamp if checkin else None
            delay = None
            status = "Missed"

            if actual_time and expected_time:
                if is_aware(actual_time):
                    actual_time = actual_time.replace(tzinfo=None)
                if is_aware(expected_time):
                    expected_time = expected_time.replace(tzinfo=None)

                delay = int((actual_time - expected_time).total_seconds() / 60)
                if delay <= 15:
                    status = "On Time"
                elif 15 < delay <= 30:
                    status = "Delayed"
                else:
                    status = "Missed"

            checkpoint_name = Checkpoint.objects.get(id=checkpoint_id).label

            ws.append([
                str(guard.id),
                guard.name,
                str(checkpoint_id),
                checkpoint_name,
                expected_time.strftime("%Y-%m-%d %H:%M"),
                actual_time.strftime("%Y-%m-%d %H:%M") if actual_time else "",
                status,
                delay if delay is not None else ""
            ])

    # Save to buffer
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    # Save to file
    # filename = f"checkin_report_{now().strftime('%Y%m%d%H%M%S')}.xlsx"
    # file_path = os.path.join(settings.MEDIA_ROOT, filename)
    # with open(file_path, 'wb') as f:
    #     f.write(buffer.getvalue())

    # # Build base_url from Site framework
    # current_site = Site.objects.get_current()
    # base_url = f"https://{current_site.domain}"

    # # Build download URL
    # file_url = f"{base_url.rstrip('/')}{settings.MEDIA_URL}{filename}"

    filename = f"checkin_report_{timezone.now().strftime('%Y%m%d%H%M%S')}.xlsx"
    file_path = os.path.join(settings.MEDIA_ROOT, filename)

    # Save file to MEDIA directory
    with open(file_path, 'wb') as f:
        f.write(buffer.getvalue())

    base_url="http://127.0.0.1:8000"
    file_url = f"{base_url.rstrip('/')}{settings.MEDIA_URL}{filename}"

    # Build downloadable URL
    #file_url = request.build_absolute_uri(os.path.join(settings.MEDIA_URL, filename))

    return {
        "file_path": file_path,
        "download_url": file_url
    }
