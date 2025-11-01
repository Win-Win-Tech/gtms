# reports/tasks.py
from celery import shared_task
from django.core.mail import EmailMessage
from django.conf import settings
import requests
import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "patrol_backend.settings")
django.setup()

print ("test")

print("test1")
report_url = "http://localhost:8000/dashboard/dashboard-checkin-report-excel/?filter=today"

try:
    response = requests.get(report_url)
    response.raise_for_status()

    download_url = response.json().get("download_url")
    if not download_url:
        raise ValueError("No download URL returned from report API")

    # Download the Excel file
    file_response = requests.get(download_url)
    file_response.raise_for_status()

    email = EmailMessage(
        subject="Daily Check-In Report",
        body="Attached is the daily check-in report.",
        #from_email=settings.DEFAULT_FROM_EMAIL,
        from_email='ravee.t@gmail.com',
        to=["info.cloudgen@gmail.com"],
    )
    email.attach("checkin_report.xlsx", file_response.content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    email.send()
except Exception as e:
    print(f"Failed to send daily report: {e}")

@shared_task
def email_daily_checkin_report():
    # Build the report URL with today's filter
    print("test1")
    report_url = "http://127.0.0.1:8000/dashboard/dashboard-checkin-report-excel/?filter=today"

    try:
        response = requests.get(report_url)
        response.raise_for_status()

        download_url = response.json().get("download_url")
        if not download_url:
            raise ValueError("No download URL returned from report API")

        # Download the Excel file
        # file_response = requests.get(download_url)
        # file_response.raise_for_status()

        email = EmailMessage(
            subject="Daily Check-In Report",
            body="Attached is the daily check-in report.",
            #from_email=settings.DEFAULT_FROM_EMAIL,
            from_email='ravee.t@gmail.com',
            to=["info.cloudgen@gmail.com"],
        )
        #email.attach("checkin_report.xlsx", file_response.content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        #email.send()
        print("download_url:", download_url)
    except Exception as e:
        print(f"Failed to send daily report: {e}")
