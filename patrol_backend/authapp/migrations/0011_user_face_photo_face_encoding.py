# Generated manually for face attendance

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("authapp", "0010_user_employee_code_alter_user_unique_together"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="face_photo",
            field=models.ImageField(
                blank=True,
                help_text="Reference photo for face attendance (enrollment)",
                null=True,
                upload_to="user_faces/",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="face_encoding",
            field=models.BinaryField(
                blank=True,
                help_text="128-d face_recognition encoding (float64 bytes); computed from face_photo",
                null=True,
            ),
        ),
    ]
