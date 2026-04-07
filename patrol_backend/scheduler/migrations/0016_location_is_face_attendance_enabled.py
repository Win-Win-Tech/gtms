# Generated manually for face attendance

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduler", "0015_checklist_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="location",
            name="is_face_attendance_enabled",
            field=models.BooleanField(
                default=False,
                help_text="If True, checkin_v4/checkout_v4 require a live face match to the user's enrolled face_photo/encoding.",
            ),
        ),
    ]
