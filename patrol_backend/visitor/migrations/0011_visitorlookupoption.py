import uuid

import django.db.models.deletion
from django.db import migrations, models


def seed_lookup_options(apps, schema_editor):
    VisitorLookupOption = apps.get_model("visitor", "VisitorLookupOption")
    Location = apps.get_model("scheduler", "Location")

    visitor_types = [
        ("guest", "Guest", 0),
        ("contractor", "Contractor", 1),
        ("client", "Client", 2),
        ("delivery", "Delivery", 3),
        ("other", "Other", 4),
    ]
    vehicle_types = [
        ("car", "Car", 0),
        ("truck", "Truck", 1),
        ("van", "Van", 2),
        ("motorcycle", "Motorcycle", 3),
        ("bus", "Bus", 4),
        ("other", "Other", 5),
    ]

    for kind, items in (
        ("visitor_type", visitor_types),
        ("vehicle_type", vehicle_types),
    ):
        for code, label, sort_order in items:
            VisitorLookupOption.objects.get_or_create(
                kind=kind,
                code=code,
                location=None,
                defaults={
                    "id": uuid.uuid4(),
                    "label": label,
                    "is_default": True,
                    "sort_order": sort_order,
                    "is_active": True,
                },
            )

    globals_by_kind = {}
    for row in VisitorLookupOption.objects.filter(location__isnull=True):
        globals_by_kind.setdefault(row.kind, []).append(row)

    for loc in Location.objects.filter(is_deleted=False):
        for kind, rows in globals_by_kind.items():
            for global_row in rows:
                VisitorLookupOption.objects.get_or_create(
                    kind=kind,
                    code=global_row.code,
                    location=loc,
                    defaults={
                        "id": uuid.uuid4(),
                        "label": global_row.label,
                        "is_default": global_row.is_default,
                        "sort_order": global_row.sort_order,
                        "is_active": global_row.is_active,
                    },
                )


def unseed_lookup_options(apps, schema_editor):
    VisitorLookupOption = apps.get_model("visitor", "VisitorLookupOption")
    VisitorLookupOption.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("scheduler", "0022_sitesetting_propagate_to_orgs"),
        ("visitor", "0010_visitorentry_site"),
    ]

    operations = [
        migrations.CreateModel(
            name="VisitorLookupOption",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "kind",
                    models.CharField(
                        choices=[("visitor_type", "Visitor Type"), ("vehicle_type", "Vehicle Type")],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                ("code", models.CharField(db_index=True, max_length=32)),
                ("label", models.CharField(max_length=64)),
                ("is_default", models.BooleanField(default=False, help_text="True for seeded system types; org custom types are False.")),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "location",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="visitor_lookup_options",
                        to="scheduler.location",
                    ),
                ),
            ],
            options={
                "ordering": ["sort_order", "label"],
                "unique_together": {("kind", "code", "location")},
            },
        ),
        migrations.AddIndex(
            model_name="visitorlookupoption",
            index=models.Index(fields=["kind", "location", "is_active"], name="visitor_vis_kind_6a8f2d_idx"),
        ),
        migrations.RunPython(seed_lookup_options, unseed_lookup_options),
        migrations.AlterField(
            model_name="visitorentry",
            name="visitor_type",
            field=models.CharField(default="guest", max_length=32),
        ),
        migrations.AlterField(
            model_name="visitorentry",
            name="vehicle_type",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
    ]
