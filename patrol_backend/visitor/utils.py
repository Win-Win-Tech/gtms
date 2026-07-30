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


def _load_font(size, bold=False, italic=False):
    from PIL import ImageFont

    candidates = []
    if italic and bold:
        candidates.extend(
            [
                "/usr/share/fonts/truetype/noto/NotoSans-BoldItalic.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-BoldItalic.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            ]
        )
    elif italic:
        candidates.extend(
            [
                "/usr/share/fonts/truetype/noto/NotoSans-Italic.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf",
                "/usr/share/fonts/truetype/noto/NotoSerif-Italic.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            ]
        )
    elif bold:
        candidates.extend(
            [
                "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
                "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            ]
        )
    else:
        candidates.extend(
            [
                "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
                "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
                "/usr/share/fonts/truetype/ubuntu/Ubuntu-M.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
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


def _wrap_text(draw, text, font, max_width):
    """Word-wrap text to fit max_width; returns list of lines."""
    words = str(text or "").split()
    if not words:
        return []
    lines = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _resolve_pass_logo_path():
    """Prefer repo GTMS logo; fall back to common relative paths."""
    from pathlib import Path

    here = Path(__file__).resolve()
    candidates = [
        here.parents[4] / "GTMS_NEw" / "src" / "assets" / "logo1.png",  # .../GTMS/
        here.parents[3] / "GTMS_NEw" / "src" / "assets" / "logo1.png",
        Path("/var/www/html/babu/c/czip/GTMS/GTMS_NEw/src/assets/logo1.png"),
    ]
    for path in candidates:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


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


def _load_script_font(size):
    """Elegant italic for Hello — Noto Serif (cleaner than decorative script)."""
    from PIL import ImageFont

    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSerif-Italic.ttf",
        "/usr/share/fonts/truetype/noto/NotoSerifDisplay-Italic.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Italic.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return _load_font(size, italic=True)


def _circular_image(img, size, ring_color=None, ring_width=3):
    """Resize image into a circle; optional colored ring."""
    from PIL import Image, ImageDraw

    src = img.convert("RGBA")
    side = min(src.width, src.height)
    left = (src.width - side) // 2
    top = (src.height - side) // 2
    src = src.crop((left, top, left + side, top + side)).resize(
        (size, size), Image.Resampling.LANCZOS
    )
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((1, 1, size - 2, size - 2), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(src, (0, 0))
    out.putalpha(mask)
    if ring_color:
        ring = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        rd = ImageDraw.Draw(ring)
        inset = max(1, ring_width // 2)
        rd.ellipse(
            (inset, inset, size - 1 - inset, size - 1 - inset),
            outline=ring_color,
            width=ring_width,
        )
        out = Image.alpha_composite(out, ring)
    return out


def _pretty_name(value):
    raw = " ".join(str(value or "").split())
    if not raw:
        return "Visitor"
    # Keep short honorifics; title-case the rest for display
    parts = []
    for w in raw.split(" "):
        if w.lower() in {"mr", "mrs", "ms", "dr"} or (len(w) <= 3 and w.isupper()):
            parts.append(w if w[0].isupper() else w.title())
        else:
            parts.append(w.title() if w.islower() or w.isupper() else w)
    return " ".join(parts)


def _text_wh(draw, text, font):
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def _center_text(draw, text, cx, y, font, fill):
    tw, th = _text_wh(draw, text, font)
    draw.text((cx - tw / 2, y), text, font=font, fill=fill)
    return th


def _center_tracked(draw, text, cx, y, font, fill, tracking=2.5):
    """Small caps look via letter tracking (subtitle)."""
    chars = list(text)
    widths = [_text_wh(draw, ch, font)[0] for ch in chars]
    total = sum(widths) + tracking * max(0, len(chars) - 1)
    x = cx - total / 2
    for ch, w in zip(chars, widths):
        draw.text((x, y), ch, font=font, fill=fill)
        x += w + tracking
    return _text_wh(draw, "Ay", font)[1]


def generate_visitor_pass_image_file(entry, qr_pil=None):
    """
    Invite-card layout with comfortable spacing (not over-tight):
    circular logo + centered title → QR → Hello/name → invite → pill → map.
    Noto Sans / Noto Serif typography, GTMS theme.
    """
    from PIL import Image, ImageDraw, ImageFilter

    GTMS = "#7C3AED"
    GTMS_RGB = (124, 58, 237, 255)
    INK = "#111827"
    BODY = "#4B5563"
    SOFT = "#9CA3AF"

    token = entry.qr_token
    if qr_pil is None:
        try:
            import qrcode

            qr = qrcode.QRCode(version=1, box_size=8, border=1)
            qr.add_data(token)
            qr.make(fit=True)
            qr_pil = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        except Exception:
            qr_pil = generate_qr_pil(token)
    if not isinstance(qr_pil, Image.Image):
        qr_pil = generate_qr_pil(token)

    visitor = getattr(entry, "visitor", None)
    location = getattr(entry, "location", None)
    host = getattr(entry, "host", None)

    org_name = (getattr(location, "name", None) or "Organisation").strip()
    address = (getattr(location, "address", None) or "").strip()
    visitor_name = _pretty_name(getattr(visitor, "visitor_name", None))
    host_name = _pretty_name(getattr(host, "name", None) or "Host")
    raw_role = (getattr(host, "role", None) or "").strip()
    host_role = ""
    if raw_role and raw_role.lower() not in {
        "guard", "admin", "fo", "so", "superadmin", "super_admin",
    }:
        host_role = raw_role.upper() if len(raw_role) <= 4 else raw_role.replace("_", " ").title()

    time_pill = None
    arrival = getattr(entry, "expected_arrival_time", None)
    if arrival is not None:
        try:
            import pytz
            from django.utils import timezone as dj_tz

            tz_name = getattr(host, "timezone", None) or "Asia/Kolkata"
            try:
                tz = pytz.timezone(tz_name)
            except Exception:
                tz = pytz.timezone("Asia/Kolkata")
            if dj_tz.is_aware(arrival):
                local_dt = arrival.astimezone(tz)
            else:
                local_dt = tz.localize(arrival)
            time_pill = f"Time : {local_dt.strftime('%-I:%M %p')} | {local_dt.strftime('%d-%m-%Y')}"
        except Exception:
            try:
                time_pill = (
                    f"Time : {arrival.strftime('%-I:%M %p')} | {arrival.strftime('%d-%m-%Y')}"
                )
            except Exception:
                time_pill = None
    if not time_pill and getattr(entry, "visit_date", None):
        time_pill = f"Date : {entry.visit_date.strftime('%d-%m-%Y')}"

    words = [w for w in org_name.split() if w and w[0].isalnum()]
    org_tag = "".join(w[0] for w in words).upper()[:6] if len(words) >= 2 else org_name
    invite_bits = [host_name]
    if host_role:
        invite_bits.append(host_role)
    invite_bits.append(f"at {org_tag}")
    # Line 1: invite to org; line 2: address (if any)
    invite_text = f"{' '.join(invite_bits)} has invited you to {org_name}."
    address_text = address or ""

    # Comfortable sizes (previous spacing, new fonts)
    title_font = _load_font(26, bold=True)
    subtitle_font = _load_font(11, bold=True)
    hello_font = _load_script_font(44)
    name_font = _load_font(38, bold=True)
    body_font = _load_font(15)
    address_font = _load_font(14)
    pill_font = _load_font(15, bold=True)
    badge_font = _load_font(11, bold=True)

    width = 720
    outer = 28
    pad_x = 28
    left = outer + pad_x
    content_w = width - 2 * (outer + pad_x)
    cx = width / 2
    top = outer + 18
    logo_size = 52
    qr_size = 260
    frame_pad = 8
    frame_w = 6

    probe = ImageDraw.Draw(Image.new("RGB", (width, 10)))
    invite_lines = _wrap_text(probe, invite_text, body_font, int(content_w * 0.92))[:3]
    address_lines = (
        _wrap_text(probe, address_text, address_font, int(content_w * 0.92))[:2]
        if address_text
        else []
    )

    logo_path = _resolve_pass_logo_path()
    logo_img = None
    if logo_path:
        try:
            logo_img = _circular_image(
                Image.open(logo_path), logo_size, ring_color=GTMS_RGB, ring_width=2
            )
        except Exception:
            logo_img = None
    if logo_img is None:
        logo_img = Image.new("RGBA", (logo_size, logo_size), (0, 0, 0, 0))
        ld = ImageDraw.Draw(logo_img)
        ld.ellipse((1, 1, logo_size - 2, logo_size - 2), fill=GTMS_RGB)
        ld.text(
            (logo_size / 2 - 4, logo_size / 2 - 7),
            "G",
            font=badge_font,
            fill=(255, 255, 255, 255),
        )

    layer = Image.new("RGBA", (width, 1100), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    y = top
    layer.paste(logo_img, (left, int(y)), logo_img)

    title_y = y + 4
    org_fit = _fit_text(draw, org_name, title_font, content_w - logo_size)
    _center_text(draw, org_fit, cx, title_y, title_font, INK)
    _center_tracked(draw, "VISITOR PASS", cx, title_y + 30, subtitle_font, SOFT, tracking=2.4)

    qr_y = int(y + logo_size + 16)
    qr_x = int((width - qr_size) // 2)
    frame = (
        qr_x - frame_pad,
        qr_y - frame_pad,
        qr_x + qr_size + frame_pad,
        qr_y + qr_size + frame_pad,
    )
    draw.rounded_rectangle(frame, radius=14, outline=GTMS, width=frame_w)
    layer.paste(
        qr_pil.resize((qr_size, qr_size), Image.Resampling.NEAREST).convert("RGBA"),
        (qr_x, qr_y),
    )

    # Comfortable rhythm (reverted from over-tight)
    y = qr_y + qr_size + frame_pad + 16
    _center_text(draw, "Hello", cx, y, hello_font, INK)
    y += 48
    name_fit = _fit_text(draw, visitor_name, name_font, content_w)
    _center_text(draw, name_fit, cx, y, name_font, GTMS)
    y += 50

    for line in invite_lines:
        _center_text(draw, line, cx, y, body_font, BODY)
        y += 20
    for line in address_lines:
        _center_text(draw, line, cx, y, address_font, SOFT)
        y += 18
    y += 14

    if time_pill:
        pill_text = _fit_text(draw, time_pill, pill_font, content_w - 36)
        tw, _ = _text_wh(draw, pill_text, pill_font)
        pill_pad_x, pill_pad_y = 22, 11
        pill_w = tw + pill_pad_x * 2
        pill_h = 22 + pill_pad_y * 2
        pill_x0 = cx - pill_w / 2
        draw.rounded_rectangle(
            (pill_x0, y, pill_x0 + pill_w, y + pill_h),
            radius=pill_h / 2,
            fill=GTMS,
        )
        draw.text(
            (pill_x0 + pill_pad_x, y + pill_pad_y),
            pill_text,
            font=pill_font,
            fill="#FFFFFF",
        )
        y += pill_h + 18

    card_bottom = y + 14
    final_h = int(card_bottom + outer)

    canvas = Image.new("RGBA", (width, final_h), (91, 33, 182, 255))
    bg = ImageDraw.Draw(canvas)
    for bx, by, r, fill in (
        (-60, 90, 220, (109, 40, 217, 255)),
        (width + 70, -20, 250, GTMS_RGB),
        (width - 10, final_h + 20, 220, GTMS_RGB),
        (-40, final_h - 30, 160, (139, 92, 246, 255)),
    ):
        bg.ellipse((bx - r, by - r, bx + r, by + r), fill=fill)

    shadow = Image.new("RGBA", (width, final_h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle(
        (outer + 4, outer + 6, width - outer + 4, card_bottom + 6),
        radius=28,
        fill=(0, 0, 0, 45),
    )
    canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(8)))
    ImageDraw.Draw(canvas).rounded_rectangle(
        (outer, outer, width - outer, card_bottom),
        radius=28,
        fill=(255, 255, 255, 255),
    )
    canvas = Image.alpha_composite(canvas, layer.crop((0, 0, width, final_h)))

    buf = BytesIO()
    canvas.convert("RGB").save(buf, format="PNG")
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
