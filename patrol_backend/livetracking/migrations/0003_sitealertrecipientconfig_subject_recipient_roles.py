# Replace flat role recipient config with subject_role → recipient_role routing.

import django.db.models.deletion
from django.db import migrations, models


def clear_old_recipient_configs(apps, schema_editor):
    """Old (site, role) rows are incompatible with subject→recipient routing."""
    SiteAlertRecipientConfig = apps.get_model("livetracking", "SiteAlertRecipientConfig")
    SiteAlertRecipientConfig.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("authapp", "0015_rename_authapp_use_user_id_site_idx_authapp_use_user_id_52d367_idx"),
        ("livetracking", "0002_userlivelocation_assigned_site_and_more"),
    ]

    operations = [
        migrations.RunPython(clear_old_recipient_configs, migrations.RunPython.noop),
        migrations.AlterUniqueTogether(
            name="sitealertrecipientconfig",
            unique_together=set(),
        ),
        migrations.RemoveIndex(
            model_name="sitealertrecipientconfig",
            name="livetrackin_site_id_ccd13d_idx",
        ),
        migrations.RemoveIndex(
            model_name="sitealertrecipientconfig",
            name="livetrackin_site_id_eb41c6_idx",
        ),
        migrations.RemoveField(
            model_name="sitealertrecipientconfig",
            name="role",
        ),
        migrations.AddField(
            model_name="sitealertrecipientconfig",
            name="subject_role",
            field=models.ForeignKey(
                help_text="Role of the user who crossed the boundary or went missing",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="site_alert_as_subject",
                to="authapp.role",
            ),
        ),
        migrations.AddField(
            model_name="sitealertrecipientconfig",
            name="recipient_role",
            field=models.ForeignKey(
                help_text="Role that should receive the alert for this subject role",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="site_alert_as_recipient",
                to="authapp.role",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="sitealertrecipientconfig",
            unique_together={("site", "subject_role", "recipient_role")},
        ),
        migrations.AddIndex(
            model_name="sitealertrecipientconfig",
            index=models.Index(
                fields=["site", "subject_role", "notify_boundary_breach"],
                name="lt_site_subj_breach_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="sitealertrecipientconfig",
            index=models.Index(
                fields=["site", "subject_role", "notify_location_missing"],
                name="lt_site_subj_missing_idx",
            ),
        ),
    ]
