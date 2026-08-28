from django.db import migrations, models


def set_all_flags_disabled(apps, schema_editor):
    Role = apps.get_model("authapp", "Role")
    Role.objects.all().update(is_allow_edit=False, is_allow_create=False)


class Migration(migrations.Migration):

    dependencies = [
        ("authapp", "0013_backfill_all_org_sites"),
    ]

    operations = [
        migrations.AddField(
            model_name="role",
            name="is_allow_edit",
            field=models.BooleanField(
                default=False,
                help_text="If true, users with this role see edit/delete actions in the web panel",
            ),
        ),
        migrations.AddField(
            model_name="role",
            name="is_allow_create",
            field=models.BooleanField(
                default=False,
                help_text="If true, users with this role see create/add actions in the web panel",
            ),
        ),
        migrations.RunPython(set_all_flags_disabled, migrations.RunPython.noop),
    ]
