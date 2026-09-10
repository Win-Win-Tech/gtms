"""
Celery tasks for automatic org report emails.

Primary task: dispatch_org_report_emails (every 15 minutes).
Legacy tasks remain as thin wrappers / deprecated.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.db import close_old_connections

from reports.models import LocationReportEmailConfig, LocationReportEmailItem, ReportEmailLog
from reports.services.email_sender import send_report_email
from reports.services.report_generator import report_generate
from reports.services.schedule_utils import (
    build_schedule_key,
    get_location_admin_user,
    get_location_timezone,
    local_now,
    matching_schedule_types,
    resolve_period,
    send_time_matches,
)
from scheduler.models import LocationSite

logger = logging.getLogger(__name__)


def _process_org_config(config: LocationReportEmailConfig, force: bool = False):
    location = config.location
    recipients = config.recipient_list()
    if not recipients:
        logger.warning("Org %s has no recipients — skip", location.name)
        return {"skipped": True, "reason": "no_recipients"}

    location_tz = get_location_timezone(location)
    now_local = local_now(location_tz)

    if not force and not send_time_matches(now_local, config.send_time):
        return {"skipped": True, "reason": "time_mismatch"}

    admin_user = get_location_admin_user(location)
    if not admin_user:
        logger.warning("Org %s has no admin user — skip", location.name)
        return {"skipped": True, "reason": "no_admin"}

    items = list(config.items.filter(is_enabled=True))
    if not items:
        return {"skipped": True, "reason": "no_items"}

    attachments = []
    logs_to_create = []
    updated_items = []

    try:
        active_sites = list(LocationSite.objects.filter(location=location, is_active=True))
    except Exception:
        active_sites = []

    for item in items:
        schedule_types = item.get_schedule_types()
        matching = (
            matching_schedule_types(schedule_types, now_local, item)
            if not force
            else [
                LocationReportEmailItem.normalize_schedule_type(t)
                for t in schedule_types
            ]
        )
        if not matching:
            continue

        sent_keys = dict(item.last_sent_keys or {})
        # Legacy single key fallback (normalize weekly_sunday / monthly_start keys)
        if not sent_keys and item.last_sent_schedule_key and item.schedule_type:
            legacy_type = LocationReportEmailItem.normalize_schedule_type(item.schedule_type)
            sent_keys[legacy_type] = item.last_sent_schedule_key

        item_touched = False

        for schedule_type in matching:
            schedule_key = build_schedule_key(now_local.date(), schedule_type)
            if not force and sent_keys.get(schedule_type) == schedule_key:
                continue

            try:
                period = resolve_period(schedule_type, item.daily_period, now_local, item)
            except Exception as exc:
                logger.exception("period resolve failed: %s", exc)
                logs_to_create.append(
                    ReportEmailLog(
                        location=location,
                        report_code=item.report_code,
                        schedule_key=schedule_key,
                        status=ReportEmailLog.STATUS_ERROR,
                        error=str(exc),
                    )
                )
                continue

            if item.site_wise and active_sites:
                scopes = [(site.id, site.name) for site in active_sites]
            else:
                scopes = [(None, location.name)]

            report_attachments = []
            for site_id, site_label in scopes:
                try:
                    atts = report_generate(
                        item.report_code,
                        location,
                        period,
                        site_id=site_id,
                        site_name=site_label,
                        user=admin_user,
                        send_pdf=item.send_pdf,
                        send_excel=item.send_excel,
                    )
                    for att in atts:
                        att["report_code"] = item.report_code
                        att["site_id"] = site_id
                        att["period_label"] = period.get("label", "")
                        att["schedule_type"] = schedule_type
                    report_attachments.extend(atts)
                except Exception as exc:
                    logger.exception(
                        "generate failed %s site=%s: %s", item.report_code, site_id, exc
                    )
                    logs_to_create.append(
                        ReportEmailLog(
                    location=location,
                            report_code=item.report_code,
                            site_id=site_id,
                            schedule_key=schedule_key,
                            period_label=period.get("label", ""),
                            status=ReportEmailLog.STATUS_ERROR,
                            error=str(exc),
                        )
                    )

            if not report_attachments:
                logs_to_create.append(
                    ReportEmailLog(
                        location=location,
                        report_code=item.report_code,
                        schedule_key=schedule_key,
                        period_label=period.get("label", ""),
                        status=ReportEmailLog.STATUS_SKIPPED,
                        error="no_data",
                    )
                )
                sent_keys[schedule_type] = schedule_key
                item_touched = True
                continue

            attachments.extend(report_attachments)
            formats = ",".join(sorted({a["format"] for a in report_attachments}))
            row_count = sum(a.get("row_count") or 0 for a in report_attachments)
            logs_to_create.append(
                ReportEmailLog(
                    location=location,
                    report_code=item.report_code,
                    schedule_key=schedule_key,
                    period_label=period.get("label", ""),
                    formats=formats,
                    row_count=row_count,
                    status=ReportEmailLog.STATUS_SENT,
                )
            )
            sent_keys[schedule_type] = schedule_key
            item_touched = True

        if item_touched:
            item.last_sent_keys = sent_keys
            item.last_sent_schedule_key = sent_keys.get(
                item.schedule_type, next(iter(sent_keys.values()), "")
            )
            updated_items.append(item)

    if not attachments:
        if logs_to_create:
            ReportEmailLog.objects.bulk_create(logs_to_create)
        if updated_items:
            LocationReportEmailItem.objects.bulk_update(
                updated_items, ["last_sent_schedule_key", "last_sent_keys"]
            )
        return {"skipped": True, "reason": "no_attachments", "logs": len(logs_to_create)}

    subject = f"Reports - {location.name} ({now_local.date().isoformat()})"
    result = send_report_email(
        location=location,
        subject=subject,
        body_lines=[],
        attachments=attachments,
        recipients=recipients,
    )

    if result.get("sent"):
        ReportEmailLog.objects.bulk_create(logs_to_create)
        if updated_items:
            LocationReportEmailItem.objects.bulk_update(
                updated_items, ["last_sent_schedule_key", "last_sent_keys"]
            )
        return {
            "sent": True,
            "attachments_count": result["attachments_count"],
            "recipients_count": len(result.get("recipients") or []),
            "reports": len(updated_items),
        }

    for log in logs_to_create:
        if log.status == ReportEmailLog.STATUS_SENT:
            log.status = ReportEmailLog.STATUS_ERROR
            log.error = result.get("reason") or "email_send_failed"
    ReportEmailLog.objects.bulk_create(logs_to_create)
    return {"sent": False, "reason": result.get("reason")}


@shared_task(name="reports.tasks.dispatch_org_report_emails")
def dispatch_org_report_emails():
    """
    Poll every 15 minutes. For each enabled org whose local clock matches send_time,
    generate only reports whose schedule matches today and that have data.
    """
    close_old_connections()
    configs = (
        LocationReportEmailConfig.objects.filter(is_enabled=True)
        .select_related("location")
        .prefetch_related("items")
    )
    summary = {"orgs": 0, "sent": 0, "skipped": 0, "errors": []}
    for config in configs:
        summary["orgs"] += 1
        try:
            result = _process_org_config(config, force=False)
            if result.get("sent"):
                summary["sent"] += 1
            else:
                summary["skipped"] += 1
                logger.info(
                    "Org %s skipped: %s",
                    config.location.name,
                    result.get("reason"),
                )
        except Exception as exc:
            logger.exception("dispatch failed for config %s", config.id)
            summary["errors"].append(str(exc))
    logger.info("dispatch_org_report_emails summary: %s", summary)
    return summary


@shared_task(name="reports.tasks.send_org_report_email_now")
def send_org_report_email_now(config_id: str):
    """Manual/test send — ignores send_time and schedule-day gates."""
    close_old_connections()
    config = (
        LocationReportEmailConfig.objects.filter(id=config_id)
        .select_related("location")
        .prefetch_related("items")
        .first()
    )
    if not config:
        return {"error": "config_not_found"}
    return _process_org_config_force_all(config)


def _process_org_config_force_all(config: LocationReportEmailConfig):
    """Test send: all enabled items, ignore schedule day and send_time and dedup."""
    location = config.location
    recipients = config.recipient_list()
    if not recipients:
        return {"skipped": True, "reason": "no_recipients"}

    location_tz = get_location_timezone(location)
    now_local = local_now(location_tz)
    admin_user = get_location_admin_user(location)
    if not admin_user:
        return {"skipped": True, "reason": "no_admin"}

    items = list(config.items.filter(is_enabled=True))
    attachments = []
    try:
        active_sites = list(LocationSite.objects.filter(location=location, is_active=True))
    except Exception:
        active_sites = []

    for item in items:
        # Prefer daily period for test when daily is selected; else first schedule type
        types = item.get_schedule_types()
        schedule_type = (
            LocationReportEmailItem.SCHEDULE_DAILY
            if LocationReportEmailItem.SCHEDULE_DAILY in types
            else types[0]
        )
        period = resolve_period(schedule_type, item.daily_period, now_local, item)
        scopes = (
            [(s.id, s.name) for s in active_sites]
            if item.site_wise and active_sites
            else [(None, location.name)]
        )
        for site_id, site_label in scopes:
            try:
                atts = report_generate(
                    item.report_code,
                    location,
                    period,
                    site_id=site_id,
                    site_name=site_label,
                    user=admin_user,
                    send_pdf=item.send_pdf,
                    send_excel=item.send_excel,
                )
                attachments.extend(atts)
            except Exception as exc:
                logger.exception("test generate failed: %s", exc)

    if not attachments:
        return {"skipped": True, "reason": "no_attachments"}

    subject = f"[TEST] Reports - {location.name} ({now_local.date().isoformat()})"
    return send_report_email(
                    location=location,
        subject=subject,
        body_lines=["", "(This is a manual test send.)"],
        attachments=attachments,
        recipients=recipients,
    )


@shared_task
def email_daily_checkin_report():
    logger.warning(
        "email_daily_checkin_report is deprecated; use dispatch_org_report_emails"
    )
    return dispatch_org_report_emails()


@shared_task
def email_monthly_attendance_summary():
    logger.warning(
        "email_monthly_attendance_summary is deprecated; use dispatch_org_report_emails"
    )
    return dispatch_org_report_emails()
