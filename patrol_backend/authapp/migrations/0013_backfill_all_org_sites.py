from django.db import migrations


def grant_all_org_sites_to_legacy_users(apps, schema_editor):
    """Existing users had org-wide access before UserSite. Keep that behaviour."""
    User = apps.get_model("authapp", "User")
    UserSite = apps.get_model("authapp", "UserSite")
    assigned_user_ids = UserSite.objects.values_list("user_id", flat=True).distinct()
    User.objects.filter(all_org_sites=False).exclude(id__in=assigned_user_ids).update(
        all_org_sites=True
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("authapp", "0012_user_all_org_sites_usersite"),
    ]

    operations = [
        migrations.RunPython(grant_all_org_sites_to_legacy_users, noop),
    ]
