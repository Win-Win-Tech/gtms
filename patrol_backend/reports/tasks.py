# reports/tasks.py
from celery import shared_task
from django.core.mail import EmailMessage
from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone as django_timezone
import os
import django
import pytz

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "patrol_backend.settings")
django.setup()

# Import the internal helper functions that the endpoint uses
from dashboard.views import (
    generate_checkin_excel_report_internal, 
    generate_attendance_excel_report_internal,
    generate_monthly_attendance_summary_excel_internal
)
from scheduler.models import Location
from authapp.models import User
from datetime import datetime, timedelta


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def send_location_report_email(location, subject, body_sections, attachments, recipients=None):
    """
    Reusable email sender for location-based reports.
    Handles dynamic number of attachments with smart formatting.
    
    Args:
        location: Location object with name and address
        subject: Email subject line
        body_sections: Dict with:
            - 'intro': Introduction text
            - 'period': Period/date information
            - 'additional': Optional additional info (legend, notes, etc.)
        attachments: List of dicts, each containing:
            {
                'file_path': str (path to file),
                'filename': str (attachment filename),
                'display_name': str (e.g., 'Check-In Report'),
                'row_count': int or None (number of records),
                'download_url': str (optional download link)
            }
        recipients: List of email addresses (defaults to settings email)
    
    Returns:
        dict: {'sent': bool, 'attachments_count': int}
    """
    if not attachments:
        print(f"  No attachments to send for {location.name}")
        return {'sent': False, 'attachments_count': 0}
    
    # Build email body
    email_body = f"""Hello,

{body_sections.get('intro', '')}

Location: {location.name}
Address: {location.address if location.address else 'N/A'}
{body_sections.get('period', '')}

Reports:"""
    
    # Add attachment details to body
    for idx, att in enumerate(attachments, 1):
        display_name = att.get('display_name', 'Report')
        row_count = att.get('row_count')
        download_url = att.get('download_url')
        
        email_body += f"\n  {idx}. {display_name}:"
        if row_count is not None and row_count > 0:
            email_body += f" Attached ({row_count} records)"
        elif row_count == 0:
            email_body += " No data available"
        else:
            email_body += " Attached"
        
        if download_url:
            email_body += f"\n     Download: {download_url}"
    
    # Add additional info (legend, notes, etc.)
    if body_sections.get('additional'):
        email_body += f"\n\n{body_sections['additional']}"
    
    email_body += f"""

Best regards,
Guard Management System
"""
    
    # Create email
    email = EmailMessage(
        subject=subject,
        body=email_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=recipients or ["sales@cloudgentechnologies.com"]
    )
    
    # Attach all files that have data
    attached_count = 0
    for att in attachments:
        file_path = att.get('file_path')
        filename = att.get('filename')
        has_data = att.get('has_data', True)  # Default to True if not specified
        
        if not has_data:
            continue  # Skip attachments without data
        
        if file_path and os.path.exists(file_path):
            with open(file_path, 'rb') as f:
                content = f.read()
            email.attach(
                filename,
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            attached_count += 1
            print(f"    ✓ Attached: {att.get('display_name', filename)}")
    
    # Only send if we have attachments
    if attached_count == 0:
        print(f"  No valid attachments - email not sent")
        return {'sent': False, 'attachments_count': 0}
    
    # Send email
    email.send()
    
    return {
        'sent': True,
        'attachments_count': attached_count
    }


# ============================================================================
# CELERY TASKS
# ============================================================================

@shared_task
def email_daily_checkin_report():
    """
    Celery task to generate and email daily check-in reports per location/organization.
    
    For each location:
    - Generates a separate report filtered by location_id
    - Only sends email if there are results for that location today
    - Includes the location/organization name in the subject and body
    
    This solves the problem of:
    - Django being single-threaded and blocking on self-requests
    - Avoiding HTTP overhead
    - Directly accessing the business logic
    - Sending location-specific reports to respective recipients
    
    The endpoint at /dashboard/dashboard-checkin-report-excel/ still works for frontend!
    """
    try:
        print("Starting daily check-in report generation per location...")
        
        # Close old database connections to avoid stale connection issues
        close_old_connections()
        
        # Get all active locations (organizations)
        locations = Location.objects.all()
        
        if not locations.exists():
            print("No locations found in the system.")
            return {"status": "no_locations", "message": "No locations to process"}
        
        print(f"Found {locations.count()} locations to process")
        
        # Track results
        results = {
            "total_locations": locations.count(),
            "emails_sent": 0,
            "skipped": 0,
            "errors": []
        }
        
        # Process each location
        for location in locations:
            try:
                print(f"\nProcessing location: {location.name} (ID: {location.id})")
                
                # Generate CHECKIN report for this specific location
                print(f"  Generating check-in report...")
                checkin_result = generate_checkin_excel_report_internal(
            filter_type='today',
            start_date=None,
            end_date=None,
            user_id=None,
                    location_id=str(location.id)  # Filter by location
                )
                
                checkin_file_path = checkin_result.get('file_path')
                checkin_filename = checkin_result.get('filename')
                checkin_row_count = checkin_result.get('row_count', 0)  # Get actual row count
                
                # Generate ATTENDANCE report for this specific location
                print(f"  Generating attendance report...")
                attendance_result = generate_attendance_excel_report_internal(
                    date_filter='today',
                    start_date=None,
                    end_date=None,
                    guard_id=None,
                    location_id=str(location.id),  # Filter by location
                    shift_id=None,
                    status_filter=None,
                    defaulters=False
                )
                
                attendance_file_path = attendance_result.get('file_path')
                attendance_filename = attendance_result.get('filename')
                attendance_row_count = attendance_result.get('row_count', 0)  # Get actual row count
                
                # Determine which reports have data
                has_checkin_data = checkin_row_count > 0
                has_attendance_data = attendance_row_count > 0
                
                print(f"  Check-in report: {checkin_row_count} rows")
                print(f"  Attendance report: {attendance_row_count} rows")
                
                # If no data in either report, skip email and clean up files
                if not has_checkin_data and not has_attendance_data:
                    print(f"  No data for location {location.name} today. Skipping email.")
                    # Clean up empty files
                    if checkin_file_path and os.path.exists(checkin_file_path):
                        os.remove(checkin_file_path)
                    if attendance_file_path and os.path.exists(attendance_file_path):
                        os.remove(attendance_file_path)
                    results["skipped"] += 1
                    continue
                
                # Clean up files that have no data
                if not has_checkin_data and checkin_file_path and os.path.exists(checkin_file_path):
                    print(f"  Check-in report has no data - removing file")
                    os.remove(checkin_file_path)
                    checkin_file_path = None
                
                if not has_attendance_data and attendance_file_path and os.path.exists(attendance_file_path):
                    print(f"  Attendance report has no data - removing file")
                    os.remove(attendance_file_path)
                    attendance_file_path = None
                
                print(f"  Reports generated successfully for {location.name}")
                print(f"    - Check-in report: {'Yes' if has_checkin_data else 'No'}")
                print(f"    - Attendance report: {'Yes' if has_attendance_data else 'No'}")
                
                # Get current date for email in location's timezone
                # Find location admin to get timezone
                location_admin = User.objects.filter(
                    location=location,
                    role='admin',
                    is_deleted=False,
                    is_active=True
                ).first()
                
                if location_admin and location_admin.timezone:
                    location_tz = pytz.timezone(location_admin.timezone)
                else:
                    # Fallback to any user's timezone in this location
                    location_user = User.objects.filter(
                        location=location,
                        is_deleted=False,
                        timezone__isnull=False
                    ).exclude(timezone='').first()
                    if location_user and location_user.timezone:
                        location_tz = pytz.timezone(location_user.timezone)
                    else:
                        # Default to UTC if no timezone found
                        location_tz = pytz.UTC
                
                # Get current datetime in location timezone
                location_now = django_timezone.now().astimezone(location_tz)
                today_date = location_now.strftime('%Y-%m-%d')
                
                # Prepare attachments list
                attachments = []
                
                if has_checkin_data and checkin_file_path:
                    attachments.append({
                        'file_path': checkin_file_path,
                        'filename': f"checkin_report_{location.name.replace(' ', '_')}_{today_date}.xlsx",
                        'display_name': 'Check-In Report',
                        'row_count': checkin_row_count,
                        'download_url': f"http://127.0.0.1:8000{settings.MEDIA_URL}{checkin_filename}",
                        'has_data': True
                    })
                else:
                    attachments.append({
                        'file_path': None,
                        'filename': None,
                        'display_name': 'Check-In Report',
                        'row_count': 0,
                        'download_url': None,
                        'has_data': False
                    })
                
                if has_attendance_data and attendance_file_path:
                    attachments.append({
                        'file_path': attendance_file_path,
                        'filename': f"attendance_report_{location.name.replace(' ', '_')}_{today_date}.xlsx",
                        'display_name': 'Attendance Report',
                        'row_count': attendance_row_count,
                        'download_url': f"http://127.0.0.1:8000{settings.MEDIA_URL}{attendance_filename}",
                        'has_data': True
                    })
                else:
                    attachments.append({
                        'file_path': None,
                        'filename': None,
                        'display_name': 'Attendance Report',
                        'row_count': 0,
                        'download_url': None,
                        'has_data': False
                    })
                
                # Send email using helper function
                email_result = send_location_report_email(
                    location=location,
                    subject=f"Daily Reports - {location.name} ({today_date})",
                    body_sections={
                        'intro': f"Please find the daily reports for {location.name}.",
                        'period': f"Date: {today_date}"
                    },
                    attachments=attachments
                )
                
                if email_result['sent']:
                    print(f"  Email sent successfully for {location.name}!")
                    results["emails_sent"] += 1
                else:
                    print(f"  Email not sent (no valid attachments)")
                    results["skipped"] += 1
                
            except Exception as e:
                error_msg = f"Failed to process location {location.name}: {str(e)}"
                print(error_msg)
                results["errors"].append(error_msg)
                import traceback
                traceback.print_exc()
                # Continue with next location even if one fails
                continue
        
        # Summary
        print("\n" + "="*50)
        print("SUMMARY:")
        print(f"Total locations: {results['total_locations']}")
        print(f"Emails sent: {results['emails_sent']}")
        print(f"Skipped (no data): {results['skipped']}")
        print(f"Errors: {len(results['errors'])}")
        print("="*50)
        
        return {
            "status": "success",
            "results": results
        }
        
    except Exception as e:
        print(f"Failed to send daily reports: {e}")
        import traceback
        traceback.print_exc()
        raise


@shared_task
def email_monthly_attendance_summary():
    """
    Celery task to generate and email monthly attendance summary reports per location.
    
    This task should be scheduled to run at the end of each month.
    
    For each location:
    - Generates a monthly attendance summary Excel report
    - Shows Present (P), Absent (A), or No Assignment (-) for each day
    - Only sends email if there are results for that location
    - Includes the location/organization name in the subject and body
    
    Report Format:
    - Calendar view with dates as columns
    - Each row shows a guard's attendance for the month
    - Easy to see attendance patterns at a glance
    """
    try:
        print("Starting monthly attendance summary report generation per location...")
        
        # Close old database connections to avoid stale connection issues
        close_old_connections()
        
        # Get all active locations (organizations)
        locations = Location.objects.all()
        
        if not locations.exists():
            print("No locations found in the system.")
            return {"status": "no_locations", "message": "No locations to process"}
        
        print(f"Found {locations.count()} locations to process")
        
        # Determine the month to report (previous month) using UTC
        # We'll calculate per location using their timezone
        utc_now = django_timezone.now()
        utc_today = utc_now.date()
        # Get last day of previous month in UTC
        first_day_this_month = utc_today.replace(day=1)
        last_day_prev_month = first_day_this_month - timedelta(days=1)
        year = last_day_prev_month.year
        month = last_day_prev_month.month
        month_str = f"{year}-{month:02d}"
        
        print(f"Generating reports for: {last_day_prev_month.strftime('%B %Y')} ({month_str})")
        
        # Track results
        results = {
            "total_locations": locations.count(),
            "emails_sent": 0,
            "skipped": 0,
            "errors": []
        }
        
        # Process each location
        for location in locations:
            try:
                print(f"\nProcessing location: {location.name} (ID: {location.id})")
                
                # Generate monthly attendance summary Excel for this specific location
                print(f"  Generating monthly attendance summary...")
                monthly_result = generate_monthly_attendance_summary_excel_internal(
                    month=month_str,
                    start_date_str=None,
                    end_date_str=None,
                    location_id=str(location.id),
                    user_id=None
                )
                
                file_path = monthly_result.get('file_path')
                filename = monthly_result.get('filename')
                row_count = monthly_result.get('row_count', 0)
                start_date = monthly_result.get('start_date')
                end_date = monthly_result.get('end_date')
                
                # Check if there's any data for this location
                if row_count == 0:
                    print(f"  No attendance data for location {location.name}. Skipping email.")
                    # Clean up empty file
                    if file_path and os.path.exists(file_path):
                        os.remove(file_path)
                    results["skipped"] += 1
                    continue
                
                print(f"  Report generated: {row_count} guards with attendance records")
                
                if not file_path or not os.path.exists(file_path):
                    print(f"  Report file not generated for {location.name}")
                    results["skipped"] += 1
                    continue
                
                print(f"  Report file created: {file_path}")
                
                # Get location timezone for date formatting
                location_admin = User.objects.filter(
                    location=location,
                    role='admin',
                    is_deleted=False,
                    is_active=True
                ).first()
                
                if location_admin and location_admin.timezone:
                    location_tz = pytz.timezone(location_admin.timezone)
                else:
                    # Fallback to any user's timezone in this location
                    location_user = User.objects.filter(
                        location=location,
                        is_deleted=False,
                        timezone__isnull=False
                    ).exclude(timezone='').first()
                    if location_user and location_user.timezone:
                        location_tz = pytz.timezone(location_user.timezone)
                    else:
                        # Default to UTC if no timezone found
                        location_tz = pytz.UTC
                
                # Format month name for email using location timezone
                # Convert last_day_prev_month to location timezone for display
                location_date = location_tz.localize(datetime.combine(last_day_prev_month, datetime.min.time()))
                month_name = location_date.strftime('%B %Y')
                
                # Build download URL
                download_url = f"http://127.0.0.1:8000{settings.MEDIA_URL}{filename}"
                
                # Prepare attachments list
                attachments = [{
                    'file_path': file_path,
                    'filename': filename,
                    'display_name': 'Monthly Attendance Summary',
                    'row_count': row_count,
                    'download_url': download_url,
                    'has_data': True
                }]
                
                # Send email using helper function
                email_result = send_location_report_email(
                    location=location,
                    subject=f"Monthly Attendance Summary - {location.name} ({month_name})",
                    body_sections={
                        'intro': f"Attached is the monthly attendance summary report for {location.name}.",
                        'period': f"Month: {month_name}\nPeriod: {start_date.strftime('%d %B %Y')} to {end_date.strftime('%d %B %Y')}",
                        'additional': """Legend:
  P = Present (checked in)
  A = Absent (no check-in)
  - = No assignment for that day"""
                    },
                    attachments=attachments
                )
                
                if email_result['sent']:
                    print(f"  Email sent successfully for {location.name}!")
                    results["emails_sent"] += 1
                else:
                    print(f"  Email not sent (no valid attachments)")
                    results["skipped"] += 1
                
            except Exception as e:
                error_msg = f"Failed to process location {location.name}: {str(e)}"
                print(error_msg)
                results["errors"].append(error_msg)
                import traceback
                traceback.print_exc()
                # Continue with next location even if one fails
                continue
        
        # Summary
        print("\n" + "="*50)
        print("MONTHLY SUMMARY REPORT - SUMMARY:")
        print(f"Month: {month_name}")
        print(f"Total locations: {results['total_locations']}")
        print(f"Emails sent: {results['emails_sent']}")
        print(f"Skipped (no data): {results['skipped']}")
        print(f"Errors: {len(results['errors'])}")
        print("="*50)
        
        return {
            "status": "success",
            "month": month_str,
            "results": results
        }
        
    except Exception as e:
        print(f"Failed to send monthly reports: {e}")
        import traceback
        traceback.print_exc()
        raise
