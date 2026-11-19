# Drop legacy "Support Dashboard" page from SUPPORT role pages.
# Dashboard is now a tab inside the single "Support Tickets" page.

from django.db import migrations


REMOVED_PAGE = "Support Dashboard"
CANONICAL_PAGES = ["Support Tickets"]


def unify_support_pages(apps, schema_editor):
    Role = apps.get_model("authapp", "Role")
    for role in Role.objects.filter(name="support").iterator():
        pages = list(role.pages or [])
        changed = False
        if REMOVED_PAGE in pages:
            pages = [p for p in pages if p != REMOVED_PAGE]
            changed = True
        if "Support Tickets" not in pages:
            pages = CANONICAL_PAGES + [p for p in pages if p not in CANONICAL_PAGES]
            changed = True
        if changed:
            role.pages = pages
            role.save(update_fields=["pages"])


def restore_support_dashboard_page(apps, schema_editor):
    Role = apps.get_model("authapp", "Role")
    for role in Role.objects.filter(name="support").iterator():
        pages = list(role.pages or [])
        if REMOVED_PAGE not in pages:
            pages.append(REMOVED_PAGE)
            role.pages = pages
            role.save(update_fields=["pages"])


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0005_support_ticketing_phase7_sla_reopen_email"),
        ("authapp", "0017_user_consent_submitted_on_user_is_consent_submitted"),
    ]

    operations = [
        migrations.RunPython(unify_support_pages, restore_support_dashboard_page),
    ]
