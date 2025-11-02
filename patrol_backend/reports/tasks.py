# reports/tasks.py
from celery import shared_task
from django.core.mail import EmailMessage
from django.conf import settings
from django.db import close_old_connections
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "patrol_backend.settings")
django.setup()

# Import the internal helper function that the endpoint uses
from dashboard.views import generate_checkin_excel_report_internal


@shared_task
def email_daily_checkin_report():
    """
    Celery task to generate and email daily check-in report.
    Uses the same internal function that the endpoint uses - no HTTP requests!
    
    This solves the problem of:
    - Django being single-threaded and blocking on self-requests
    - Avoiding HTTP overhead
    - Directly accessing the business logic
    
    The endpoint at /dashboard/dashboard-checkin-report-excel/ still works for frontend!
    """
    try:
        print("Starting daily check-in report generation...")
        
        # Close old database connections to avoid stale connection issues
        close_old_connections()
        
        # Call the SAME internal function that the endpoint uses
        # NO HTTP requests - direct function call!
        result = generate_checkin_excel_report_internal(
            filter_type='today',
            start_date=None,
            end_date=None,
            user_id=None,
            location_id=None
        )
        
        file_path = result.get('file_path')
        filename = result.get('filename')
        
        if not file_path or not os.path.exists(file_path):
            raise ValueError(f"Report file not generated: {file_path}")
        
        print(f"Report generated successfully: {file_path}")
        
        # Read the Excel file from disk
        with open(file_path, 'rb') as f:
            excel_content = f.read()
        
        # Build download URL for email body
        download_url = f"http://127.0.0.1:8000{settings.MEDIA_URL}{filename}"
        
        # Send email with attachment
        email = EmailMessage(
            subject="Daily Check-In Report",
            body=f"Attached is the daily check-in report.\n\nYou can also download it from: {download_url}",
            from_email=settings.DEFAULT_FROM_EMAIL,  # ravit@cloudgentechnologies.com
            to=["sales@cloudgentechnologies.com"],
        )
        email.attach(
            "checkin_report.xlsx",
            excel_content,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        email.send()
        
        print("Email sent successfully!")
        return {"status": "success", "file_path": file_path}
        
    except Exception as e:
        print(f"Failed to send daily report: {e}")
        import traceback
        traceback.print_exc()
        raise
