from datetime import date, datetime
from decimal import Decimal
import uuid

from django.db.models.fields.files import FieldFile
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver

from .audit_context import get_audit_source, get_audit_user
from .models import AttendanceCheckin, GlobalAuditLog


def _serialize_value(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, FieldFile):
        # Never access .url here; it raises ValueError when no file is associated.
        return v.name or None
    return v


def _instance_to_dict(instance):
    data = {}
    for f in instance._meta.concrete_fields:
        name = f.name
        if f.is_relation and hasattr(f, "attname"):
            raw = getattr(instance, f.attname, None)
            data[name] = _serialize_value(raw)
        else:
            raw = getattr(instance, name, None)
            data[name] = _serialize_value(raw)
    return data


def _changed_fields(old_data, new_data):
    if old_data is None:
        return sorted(list((new_data or {}).keys()))
    keys = set((old_data or {}).keys()) | set((new_data or {}).keys())
    changed = [k for k in keys if (old_data or {}).get(k) != (new_data or {}).get(k)]
    return sorted(changed)


@receiver(pre_save, sender=AttendanceCheckin)
def attendance_pre_save_capture(sender, instance, **kwargs):
    if instance.pk:
        old = sender.objects.filter(pk=instance.pk).first()
        instance._audit_old_data = _instance_to_dict(old) if old else None
    else:
        instance._audit_old_data = None


@receiver(post_save, sender=AttendanceCheckin)
def attendance_post_save_audit(sender, instance, created, **kwargs):
    old_data = getattr(instance, "_audit_old_data", None)
    new_data = _instance_to_dict(instance)
    event_type = "create" if created else "update"
    changed = _changed_fields(old_data, new_data)

    # avoid no-op audit rows on updates with no data changes
    if event_type == "update" and not changed:
        return

    GlobalAuditLog.objects.create(
        app_label=instance._meta.app_label,
        model_name=instance._meta.model_name,
        object_pk=str(instance.pk),
        event_type=event_type,
        location_id=getattr(instance, "org_location_id", None),
        old_data=old_data if event_type == "update" else None,
        new_data=new_data,
        changed_fields=changed,
        changed_by=get_audit_user(),
        source=get_audit_source(),
    )


@receiver(pre_delete, sender=AttendanceCheckin)
def attendance_pre_delete_capture(sender, instance, **kwargs):
    instance._audit_old_data = _instance_to_dict(instance)


@receiver(post_delete, sender=AttendanceCheckin)
def attendance_post_delete_audit(sender, instance, **kwargs):
    old_data = getattr(instance, "_audit_old_data", None) or _instance_to_dict(instance)
    GlobalAuditLog.objects.create(
        app_label=instance._meta.app_label,
        model_name=instance._meta.model_name,
        object_pk=str(instance.pk),
        event_type="delete",
        location_id=getattr(instance, "org_location_id", None),
        old_data=old_data,
        new_data=None,
        changed_fields=sorted(list((old_data or {}).keys())),
        changed_by=get_audit_user(),
        source=get_audit_source(),
    )

