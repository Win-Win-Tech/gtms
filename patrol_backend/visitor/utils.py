"""Visitor helpers: location scoping, date filters, QR generation."""
import secrets
import uuid
from datetime import datetime, timedelta
from io import BytesIO

from django.core.files.base import ContentFile

from patrol_backend.utils.timezone_utils import (
    convert_date_range_to_utc,
    get_user_timezone_from_request,
    get_user_today,
)


def resolve_location_for_request(request, location_id=None):
    """
    Non-superuser: always use request.user.location.
    Superuser: optional location_id query/body, else user.location.
    Returns (location_id_str_or_None, error_message_or_None).
    """
    user = request.user
    if getattr(user, "is_superuser", False):
        loc = location_id or getattr(user, "location_id", None)
        return (str(loc) if loc else None, None)

    user_loc = getattr(user, "location_id", None)
    if not user_loc:
        return None, "User is not mapped to a location"
    if location_id and str(location_id) != str(user_loc):
        return None, "You can only access visitors for your own location"
    return str(user_loc), None


def _date_range_q(start_date, end_date, start_utc, end_utc):
    """
    Filter by visit_date when set (day the visit is for).
    Fallback when visit_date is null: check_in_time, else created_on.
    """
    from django.db.models import Q

    end_exclusive = end_utc + timedelta(days=1)
    return (
        Q(visit_date__gte=start_date, visit_date__lte=end_date)
        | Q(
            visit_date__isnull=True,
            check_in_time__gte=start_utc,
            check_in_time__lt=end_exclusive,
        )
        | Q(
            visit_date__isnull=True,
            check_in_time__isnull=True,
            created_on__gte=start_utc,
            created_on__lt=end_exclusive,
        )
    )


def apply_checkin_date_filter(queryset, request, location_id, date_filter, start_date=None, end_date=None):
    """Filter by visit_date (primary), else check_in_time / created_on."""
    user_tz = get_user_timezone_from_request(request, location_id=location_id)
    user_today = get_user_today(user_tz)
    date_filter = (date_filter or "today").lower()

    if date_filter in ("all", ""):
        return queryset

    def _apply(range_start, range_end):
        start_utc, end_utc = convert_date_range_to_utc(range_start, range_end, user_tz)
        return queryset.filter(_date_range_q(range_start, range_end, start_utc, end_utc))

    if date_filter == "today":
        return _apply(user_today, user_today)

    if date_filter in ("upcoming", "future"):
        # Visits after today (scheduled / future invite days)
        return queryset.filter(visit_date__gt=user_today)

    if date_filter in ("week", "this_week"):
        start_week = user_today - timedelta(days=user_today.weekday())
        end_week = start_week + timedelta(days=6)
        return _apply(start_week, end_week)

    if date_filter in ("month", "this_month"):
        start_of_month = user_today.replace(day=1)
        if user_today.month == 12:
            end_of_month = user_today.replace(year=user_today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_of_month = user_today.replace(month=user_today.month + 1, day=1) - timedelta(days=1)
        return _apply(start_of_month, end_of_month)

    if date_filter == "custom" and start_date and end_date:
        try:
            start_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
            return _apply(start_obj, end_obj)
        except ValueError as exc:
            raise ValueError("Invalid date format. Use YYYY-MM-DD.") from exc

    return _apply(user_today, user_today)


def make_qr_token():
    return secrets.token_urlsafe(24)[:48] or str(uuid.uuid4()).replace("-", "")


def _delete_field_file(field_file):
    """Delete stored file from storage without saving the model."""
    if not field_file:
        return
    try:
        field_file.delete(save=False)
    except Exception:
        pass


def _load_font(size, bold=False):
    from PIL import ImageFont

    candidates = []
    if bold:
        candidates.extend(
            [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
            ]
        )
    else:
        candidates.extend(
            [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            ]
        )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_text(draw, text, font, max_width):
    value = str(text or "")
    if not value:
        return "—"
    while value and draw.textlength(value, font=font) > max_width:
        if len(value) <= 4:
            break
        value = f"{value[:-4]}…"
    return value


def generate_qr_pil(token):
    try:
        import qrcode
        from PIL import Image
    except ImportError as exc:
        raise ImportError(
            "Package 'qrcode' is required for visitor QR generation. "
            "Install with: pip install 'qrcode[pil]==8.0'"
        ) from exc

    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(token)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    if not isinstance(img, Image.Image):
        img = img.convert("RGB")
    return img.convert("RGB")


def generate_qr_image_file(token):
    """Return ContentFile PNG for the given QR token."""
    img = generate_qr_pil(token)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return ContentFile(buf.getvalue(), name=f"visitor_qr_{token[:12]}.png")


def generate_visitor_pass_image_file(entry, qr_pil=None):
    """
    Build visitor ID-card PNG: organisation, visitor name, visit date, host, QR.
    """
    from PIL import Image, ImageDraw

    token = entry.qr_token
    if qr_pil is None:
        qr_pil = generate_qr_pil(token)

    visitor = getattr(entry, "visitor", None)
    location = getattr(entry, "location", None)
    host = getattr(entry, "host", None)

    org_name = getattr(location, "name", None) or "Organisation"
    visitor_name = getattr(visitor, "visitor_name", None) or "Visitor"
    host_name = getattr(host, "name", None) or "—"
    ic_number = getattr(visitor, "ic_passport_number", None) or "—"
    if entry.visit_date:
        visit_label = entry.visit_date.strftime("%d/%m/%Y")
    else:
        visit_label = "—"

    width, height = 640, 900
    canvas = Image.new("RGB", (width, height), "#F3F0FA")
    draw = ImageDraw.Draw(canvas)

    pad = 36
    card = (pad, pad, width - pad, height - pad)
    draw.rounded_rectangle(card, radius=28, fill="#FFFFFF")

    header_bottom = pad + 130
    draw.rounded_rectangle((pad, pad, width - pad, header_bottom + 20), radius=28, fill="#7C3AED")
    draw.rectangle((pad, header_bottom - 10, width - pad, header_bottom + 20), fill="#7C3AED")

    title_font = _load_font(22, bold=True)
    org_font = _load_font(26, bold=True)
    name_font = _load_font(32, bold=True)
    label_font = _load_font(18, bold=True)
    value_font = _load_font(22, bold=True)
    footer_font = _load_font(16, bold=True)
    small_font = _load_font(14)

    def center_text(text, y, font, fill):
        fitted = _fit_text(draw, text, font, width - pad * 2 - 48)
        tw = draw.textlength(fitted, font=font)
        draw.text(((width - tw) / 2, y), fitted, font=font, fill=fill)

    center_text("VISITOR PASS", pad + 28, title_font, "#FFFFFF")
    center_text(org_name, pad + 72, org_font, "#FFFFFF")
    center_text(visitor_name, pad + 175, name_font, "#1F2937")

    def meta_row(label, value, y):
        draw.text((pad + 48, y), label, font=label_font, fill="#6B7280")
        fitted = _fit_text(draw, value, value_font, width - pad * 2 - 200)
        draw.text((pad + 180, y), fitted, font=value_font, fill="#111827")

    meta_row("Visit date", visit_label, pad + 240)
    meta_row("Host", host_name, pad + 284)
    meta_row("IC / Passport", ic_number, pad + 328)

    qr_size = 280
    qr_x = (width - qr_size) // 2
    qr_y = pad + 390
    frame = (qr_x - 16, qr_y - 16, qr_x + qr_size + 16, qr_y + qr_size + 16)
    draw.rounded_rectangle(frame, radius=18, fill="#F9FAFB", outline="#E5E7EB", width=2)
    qr_resized = qr_pil.resize((qr_size, qr_size))
    canvas.paste(qr_resized, (qr_x, qr_y))

    center_text("Scan at gatehouse", qr_y + qr_size + 28, footer_font, "#7C3AED")
    center_text("Present this pass on arrival", height - pad - 40, small_font, "#9CA3AF")

    buf = BytesIO()
    canvas.save(buf, format="PNG")
    buf.seek(0)
    short = (token or str(uuid.uuid4()))[:12]
    return ContentFile(buf.getvalue(), name=f"visitor_pass_{short}.png")


def refresh_entry_qr_and_pass(entry, *, regenerate_token=False, save=True):
    """
    Delete old QR/pass files and regenerate.
    Call whenever QR token or ID-card info changes (create, resubmit, reschedule).
    """
    if regenerate_token or not entry.qr_token:
        entry.qr_token = make_qr_token()

    _delete_field_file(entry.qr_image)
    _delete_field_file(entry.pass_image)

    qr_pil = generate_qr_pil(entry.qr_token)
    qr_buf = BytesIO()
    qr_pil.save(qr_buf, format="PNG")
    qr_buf.seek(0)
    short = entry.qr_token[:12]
    entry.qr_image.save(f"visitor_qr_{short}.png", ContentFile(qr_buf.getvalue()), save=False)
    entry.pass_image.save(
        f"visitor_pass_{short}.png",
        generate_visitor_pass_image_file(entry, qr_pil=qr_pil),
        save=False,
    )

    if save:
        entry.save()
    return entry
