from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def convert_invalid_and_stamp_status(apps, schema_editor):
    """INVALID → REFERRED_BACK. Backfill status_entered_at for the auto-close clock."""
    SupportTicket = apps.get_model("support", "SupportTicket")
    now = timezone.now()
    for ticket in SupportTicket.objects.all().iterator():
        fields = []
        if ticket.status == "INVALID":
            ticket.status = "REFERRED_BACK"
            fields.append("status")
            if not (ticket.referred_back_reason or "").strip():
                ticket.referred_back_reason = ticket.invalidation_reason or ""
                fields.append("referred_back_reason")
            if not ticket.referred_back_at:
                ticket.referred_back_at = ticket.updated_on or ticket.created_on or now
                fields.append("referred_back_at")
        if not ticket.status_entered_at:
            if ticket.status == "CLOSED" and ticket.closed_at:
                ticket.status_entered_at = ticket.closed_at
            elif ticket.status == "RESOLVED" and ticket.resolved_at:
                ticket.status_entered_at = ticket.resolved_at
            elif ticket.status == "REFERRED_BACK" and ticket.referred_back_at:
                ticket.status_entered_at = ticket.referred_back_at
            else:
                ticket.status_entered_at = ticket.created_on or now
            fields.append("status_entered_at")
        if fields:
            ticket.save(update_fields=fields)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0006_unify_support_tickets_pages"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="supportticket",
            name="auto_closed",
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text="True when Celery closed the ticket after 24h in RESOLVED or REFERRED_BACK.",
            ),
        ),
        migrations.AddField(
            model_name="supportticket",
            name="referred_back_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="supportticket",
            name="referred_back_reason",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="supportticket",
            name="status_entered_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text="When the ticket entered its current status. Auto-close clock for RESOLVED and REFERRED_BACK.",
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="ticketassignment",
            name="action",
            field=models.CharField(
                choices=[
                    ("PICKUP", "Pickup"),
                    ("ASSIGN", "Assign"),
                    ("REASSIGN", "Reassign"),
                    ("TRANSFER", "Transfer"),
                    ("REFER_BACK", "Refer back"),
                ],
                max_length=16,
            ),
        ),
        migrations.RunPython(convert_invalid_and_stamp_status, noop_reverse),
        migrations.AlterField(
            model_name="supportticket",
            name="status",
            field=models.CharField(
                choices=[
                    ("OPEN", "Open"),
                    ("IN_PROGRESS", "In Progress"),
                    ("RESOLVED", "Resolved"),
                    ("CLOSED", "Closed"),
                    ("REFERRED_BACK", "Referred Back"),
                ],
                db_index=True,
                default="OPEN",
                max_length=16,
            ),
        ),
        migrations.AddIndex(
            model_name="supportticket",
            index=models.Index(
                fields=["status", "status_entered_at"],
                name="support_tkt_status_entered",
            ),
        ),
    ]
