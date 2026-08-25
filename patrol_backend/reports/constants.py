"""Report catalog metadata for auto email."""

from .models import LocationReportEmailItem as Item

REPORT_CATALOG = [
    {
        "code": Item.REPORT_CHECKIN,
        "label": "Check-in Report",
        "supports_pdf": True,
        "supports_excel": True,
        "supports_site_wise": True,
        "default_schedule": Item.SCHEDULE_DAILY,
    },
    {
        "code": Item.REPORT_ATTENDANCE,
        "label": "Attendance Report",
        "supports_pdf": True,
        "supports_excel": True,
        "supports_site_wise": True,
        "default_schedule": Item.SCHEDULE_DAILY,
    },
    {
        "code": Item.REPORT_ROLLCALL,
        "label": "Roll Call Report",
        "supports_pdf": True,
        "supports_excel": True,
        "supports_site_wise": True,
        "default_schedule": Item.SCHEDULE_DAILY,
    },
    {
        "code": Item.REPORT_INCIDENT,
        "label": "Incident Report",
        "supports_pdf": True,
        "supports_excel": True,
        "supports_site_wise": True,
        "default_schedule": Item.SCHEDULE_DAILY,
    },
    {
        "code": Item.REPORT_VISITOR_ENTRIES,
        "label": "Visitor Entries",
        "supports_pdf": True,
        "supports_excel": True,
        "supports_site_wise": True,
        "default_schedule": Item.SCHEDULE_DAILY,
    },
    {
        "code": Item.REPORT_VEHICLE_MOVEMENT,
        "label": "Vehicle Movement",
        "supports_pdf": True,
        "supports_excel": True,
        "supports_site_wise": True,
        "default_schedule": Item.SCHEDULE_DAILY,
    },
    {
        "code": Item.REPORT_MONTHLY_ATTENDANCE,
        "label": "Monthly Attendance Summary",
        "supports_pdf": False,
        "supports_excel": True,
        "supports_site_wise": True,
        "default_schedule": Item.SCHEDULE_MONTHLY_START,
    },
    {
        "code": Item.REPORT_MONTHLY_LOCATION,
        "label": "Monthly Location Summary",
        "supports_pdf": False,
        "supports_excel": True,
        "supports_site_wise": True,
        "default_schedule": Item.SCHEDULE_MONTHLY_START,
    },
]

REPORT_CATALOG_BY_CODE = {r["code"]: r for r in REPORT_CATALOG}
