# Seed global SUPPORT role (ticket pages only) + default ticket categories.

from django.db import migrations


SUPPORT_PAGES = ["Support Tickets"]

DEFAULT_CATEGORIES = [
    ("Attendance / GPS", 10),
    ("Check-in / Check-out", 20),
    ("Live Tracking", 30),
    ("App / Login", 40),
    ("Other", 100),
]


def seed_forward(apps, schema_editor):
    Role = apps.get_model("authapp", "Role")
    TicketCategory = apps.get_model("support", "TicketCategory")

    Role.objects.update_or_create(
        name="support",
        location=None,
        defaults={
            "is_default": True,
            "is_allow_webapp": True,
            "is_allow_edit": True,
            "is_allow_create": True,
            "pages": SUPPORT_PAGES,
        },
    )

    for name, sort_order in DEFAULT_CATEGORIES:
        TicketCategory.objects.update_or_create(
            name=name,
            defaults={"is_active": True, "sort_order": sort_order},
        )


def seed_backward(apps, schema_editor):
    Role = apps.get_model("authapp", "Role")
    TicketCategory = apps.get_model("support", "TicketCategory")
    Role.objects.filter(name="support", location__isnull=True).delete()
    TicketCategory.objects.filter(name__in=[c[0] for c in DEFAULT_CATEGORIES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0001_support_ticketing_phase1"),
        ("authapp", "0017_user_consent_submitted_on_user_is_consent_submitted"),
    ]

    operations = [
        migrations.RunPython(seed_forward, seed_backward),
    ]
