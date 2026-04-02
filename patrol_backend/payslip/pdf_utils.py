"""
Payslip PDF (ReportLab): classic company layout — outer border, logo + centered company,
gray period bar, two-column employee grid, side-by-side earnings/deductions, summary, words, leave line.

Optional employee fields (Team, Designation, DOJ, etc.) resolve from:
- `template.layout_config["employee_meta"]` (dict), and/or
- `record.field_values` keys (e.g. TEAM, DESIGNATION, DOJ, BRANCH, PAY_MODE).
"""
from __future__ import annotations

import calendar
import logging
from decimal import Decimal
from io import BytesIO

from django.conf import settings
from django.core.files.storage import default_storage

from .models import PayslipField
from .services import parse_month

logger = logging.getLogger(__name__)


def _safe_hex_color(hex_str: str, fallback: str = "#111111"):
    from reportlab.lib import colors

    s = str(hex_str or "").strip()
    if len(s) == 7 and s.startswith("#"):
        try:
            return colors.HexColor(s)
        except Exception:
            pass
    return colors.HexColor(fallback)


def _pdf_escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )


def _fmt_rs_cell(val) -> str:
    """Amount for table cells (reference style: 6000 or 1,234.50)."""
    if val is None or val == "":
        return "-"
    try:
        d = val if isinstance(val, Decimal) else Decimal(str(val))
    except Exception:
        return str(val)
    if d == d.to_integral():
        return f"{int(d):,}"
    s = f"{d:,.2f}"
    if s.endswith(".00"):
        return f"{int(d):,}"
    return s


def _fmt_day_1(val) -> str:
    if val is None or val == "":
        return "-"
    try:
        d = val if isinstance(val, Decimal) else Decimal(str(val))
    except Exception:
        return str(val)
    return f"{d:.1f}"


def _fmt_display(field, raw) -> str:
    if raw is None or raw == "":
        return "-"
    if field.field_type in ("EARNING", "DEDUCTION"):
        return _fmt_rs_cell(raw)
    if getattr(field, "field_code", "") in {"MONTH_DAYS", "WORKING_DAYS", "PRESENT_DAYS", "HALF_DAYS", "ABSENT_DAYS", "PAID_DAYS"}:
        return _fmt_day_1(raw)
    return str(raw)


def _fv_any(field_values: dict, *keys: str):
    for k in keys:
        if k in field_values and field_values[k] not in (None, ""):
            return field_values[k]
    return None


def _xml(s: str) -> str:
    if s is None:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _meta_line(record, template, field_values: dict) -> dict:
    """Values for classic left/right employee rows (FaceReg-like)."""
    lc = getattr(template, "layout_config", None) or {}
    em = lc.get("employee_meta") if isinstance(lc, dict) else None
    em = em if isinstance(em, dict) else {}

    def pick(*keys, em_keys=None):
        for k in keys:
            v = _fv_any(field_values, k, k.upper(), k.lower())
            if v is not None:
                return str(v)
        if em_keys:
            for ek in em_keys:
                if ek in em and em[ek] not in (None, ""):
                    return str(em[ek])
        return ""

    profile = record.payroll_profile
    return {
        "name": getattr(record.user, "name", None) or "-",
        "code": profile.employee_code or "-",
        "team": pick("TEAM", "team", em_keys=("team", "Team")),
        "designation": pick("DESIGNATION", "designation", em_keys=("designation", "Designation")) or (getattr(record.user, "role", None) or "-").title(),
        "working_days": _fmt_day_1(record.working_days),
        "net_payable_days": _fmt_day_1(record.paid_days),
        "doj": pick("DOJ", "doj", "DATE_OF_JOINING", em_keys=("doj", "date_of_joining")),
        "uan": profile.uan or pick("UAN", "PF_UAN", em_keys=("uan",)),
        "pay_mode": pick("PAY_MODE", "pay_mode", em_keys=("pay_mode",)),
        "bank": profile.bank_name or pick("BANK", "bank", em_keys=("bank",)),
        "account": profile.account_number or pick("ACCOUNT", "account_no", em_keys=("account",)),
        "branch": pick("BRANCH", "branch", em_keys=("branch",)) or (getattr(record.location, "name", None) or ""),
    }


def _month_title_and_range(month_str: str) -> tuple[str, str]:
    """('February 2026', '01-02-2026 to 28-02-2026')."""
    try:
        y, m, first, last = parse_month(month_str)
        name = f"{calendar.month_name[m]} {y}"
        dr = f"{first.strftime('%d-%m-%Y')} to {last.strftime('%d-%m-%Y')}"
        return name, dr
    except Exception:
        return month_str, ""


def _net_pay_words(amount: Decimal) -> str:
    try:
        from num2words import num2words

        rupees = int(amount)
        paise = int((amount * 100) % 100)
        words = num2words(rupees, lang="en_IN").replace("-", " ").title()
        if paise:
            return f"{words} Rupees and {num2words(paise, lang='en_IN')} Paise Only"
        return f"{words} Rupees Only"
    except Exception:
        return ""


def _employer_c_total(field_values: dict) -> Decimal:
    s = Decimal("0")
    for k, v in field_values.items():
        if not str(k).upper().startswith("ER_"):
            continue
        try:
            s += Decimal(str(v))
        except Exception:
            continue
    return s


def _on_page_border(canvas, doc):
    from reportlab.lib import colors

    canvas.saveState()
    canvas.setStrokeColor(colors.black)
    canvas.setLineWidth(0.8)
    w, h = doc.pagesize
    m = doc.leftMargin
    canvas.rect(m, m, w - 2 * m, h - 2 * m)
    canvas.restoreState()


def _build_reportlab_pdf(record) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image as RLImage
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    template = record.template
    profile = record.payroll_profile
    user = record.user
    field_values = dict(record.field_values or {})
    snap = dict(record.attendance_snapshot or {})
    meta = _meta_line(record, template, field_values)

    fields = list(
        PayslipField.objects.filter(
            field_config_id=record.field_config_id,
            is_deleted=False,
            is_visible=True,
        ).order_by("display_order", "field_code")
    )

    earnings = [f for f in fields if f.field_type == "EARNING"]
    deductions = [f for f in fields if f.field_type == "DEDUCTION"]
    infos = [f for f in fields if f.field_type == "INFO"]

    styles = getSampleStyleSheet()
    row_pressure = max(len(earnings), len(deductions), 1) + len(infos)
    if row_pressure >= 24:
        font_sz = 7
    elif row_pressure >= 16:
        font_sz = 8
    else:
        font_sz = 9

    body = ParagraphStyle(
        "PSBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=font_sz,
        leading=font_sz + 2,
        alignment=TA_LEFT,
    )
    body_center = ParagraphStyle(
        "PSCenter",
        parent=body,
        alignment=TA_CENTER,
    )
    body_right = ParagraphStyle(
        "PSRight",
        parent=body,
        alignment=TA_RIGHT,
    )
    company_title = ParagraphStyle(
        "PSCompany",
        parent=body_center,
        fontSize=font_sz + 2,
        leading=font_sz + 4,
        spaceAfter=2,
    )
    header_text_color = _safe_hex_color(getattr(template, "header_text_color", "#111111"), "#111111")
    footer_color = _safe_hex_color(getattr(template, "footer_color", "#64748b"), "#64748b")

    gray_bar = ParagraphStyle(
        "PSGrayBar",
        parent=body_center,
        fontName="Helvetica-Bold",
        fontSize=font_sz,
        leading=font_sz + 2,
    )

    page_size = A4
    if getattr(template, "orientation", "portrait") == "landscape":
        page_size = landscape(A4)

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=page_size,
        rightMargin=24,
        leftMargin=24,
        topMargin=26,
        bottomMargin=24,
        onFirstPage=_on_page_border,
        onLaterPages=_on_page_border,
    )

    # Keep slip narrower and centered (not full page width).
    cw_pt = min(page_size[0] - 48, 540)
    cw = (cw_pt / 72.0) * inch

    story = []

    def _center(tbl):
        tbl.hAlign = "CENTER"
        return tbl

    # --- Logo + centered company ---
    company_name = (template.company_name or "").strip() or "Company"
    addr = "<br/>".join(_xml(line) for line in (template.company_address or "").split("\n"))
    extra = []
    if template.company_email:
        extra.append(f"Email: {_xml(template.company_email)}")
    if template.company_phone:
        extra.append(f"Ph: {_xml(template.company_phone)}")
    if template.company_gstin:
        extra.append(f"GSTIN: {_xml(template.company_gstin)}")
    extra_s = " &nbsp;|&nbsp; ".join(extra) if extra else ""

    header_name_style = ParagraphStyle(
        "PSHeaderName",
        parent=company_title,
        alignment=TA_CENTER,
        fontSize=font_sz + 1,
        leading=font_sz + 3,
        textColor=header_text_color,
    )
    header_meta_style = ParagraphStyle(
        "PSHeaderMeta",
        parent=body_center,
        alignment=TA_CENTER,
        fontSize=max(font_sz - 1, 7),
        leading=font_sz + 1,
    )

    logo_cell = ""
    if template.company_logo and getattr(template.company_logo, "name", None):
        try:
            raw = None
            try:
                template.company_logo.open("rb")
                raw = template.company_logo.read()
                template.company_logo.close()
            except Exception:
                raw = None

            if not raw:
                try:
                    with default_storage.open(template.company_logo.name, "rb") as f:
                        raw = f.read()
                except Exception:
                    raw = None

            if raw:
                logo_cell = RLImage(BytesIO(raw), width=1.15 * inch, height=0.62 * inch, kind="proportional")
        except Exception:
            logo_cell = ""

    right_header_rows = [
        [Paragraph(f"<b>{_xml(company_name)}</b>", header_name_style)],
        [Paragraph(addr or "&nbsp;", header_meta_style)],
        [Paragraph(extra_s or "&nbsp;", header_meta_style)],
    ]
    right_header = Table(right_header_rows, colWidths=[None])
    right_header.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LINEBELOW", (0, 0), (0, 0), 0.5, colors.black),
                ("LINEBELOW", (0, 1), (0, 1), 0.5, colors.black),
            ]
        )
    )

    if logo_cell:
        logo_w = 1.25 * inch
        head = Table([[logo_cell, right_header]], colWidths=[logo_w, cw - logo_w])
        head.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
                    ("LINEAFTER", (0, 0), (0, 0), 0.5, colors.black),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (0, 0), (0, 0), "CENTER"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("MINROWHEIGHT", (0, 0), (-1, -1), 56),
                ]
            )
        )
    else:
        head = Table([[right_header]], colWidths=[cw])
        head.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
    story.append(_center(head))
    story.append(Spacer(1, 0))

    # --- Gray period bar ---
    mname, dr = _month_title_and_range(record.month)
    period_txt = f"Pay slip for the Month of {mname}"
    if dr:
        period_txt += f" ({dr})"
    bar = Table([[Paragraph(period_txt, gray_bar)]], colWidths=[cw])
    bar.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#e8e8e8")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(_center(bar))
    story.append(Spacer(1, 0))

    # --- Employee grid (FaceReg-style 6 rows x 4 cols) ---
    grid_style = TableStyle(
        [
            ("FONT", (0, 0), (-1, -1), "Helvetica", font_sz - 1),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]
    )
    emp_rows = [
        ["Employee Name", meta["name"], "Employee Code", meta["code"]],
        ["Team", meta["team"] or "-", "Designation", meta["designation"] or "-"],
        ["Working Days", meta["working_days"], "Net Payable Days", meta["net_payable_days"]],
        ["Date of Joining", meta["doj"] or "-", "PF UAN", meta["uan"] or "-"],
        ["Pay Mode", meta["pay_mode"] or "-", "Bank", meta["bank"] or "-"],
        ["Branch", meta["branch"] or "-", "Account No.", meta["account"] or "-"],
    ]
    emp_w = [cw * 0.22, cw * 0.28, cw * 0.22, cw * 0.28]
    emp_t = Table(emp_rows, colWidths=emp_w)
    emp_t.setStyle(grid_style)
    story.append(_center(emp_t))
    story.append(Spacer(1, 0))

    # --- Earnings | Deductions (side by side) ---
    nmax = max(len(earnings), len(deductions), 1)
    ed_rows = []
    ed_rows.append(
        [
            Paragraph("<b>Gross Salary</b>", body),
            Paragraph("<b>Amount (Rs.)</b>", body_right),
            Paragraph("<b>Deductions</b>", body),
            Paragraph("<b>Amount (Rs.)</b>", body_right),
        ]
    )
    for i in range(nmax):
        el = earnings[i] if i < len(earnings) else None
        dl = deductions[i] if i < len(deductions) else None
        if el:
            ename = _xml(el.field_name)
            eamt = _fmt_rs_cell(field_values.get(el.field_code))
        else:
            ename = " "
            eamt = " "
        if dl:
            dname = _xml(dl.field_name)
            damt = _fmt_rs_cell(field_values.get(dl.field_code))
        else:
            dname = " "
            damt = " "
        ed_rows.append(
            [
                Paragraph(ename, body),
                Paragraph(eamt, body_right),
                Paragraph(dname, body),
                Paragraph(damt, body_right),
            ]
        )
    ed_rows.append(
        [
            Paragraph("<b>Gross Salary (A)</b>", body),
            Paragraph(f"<b>{_fmt_rs_cell(record.total_earnings)}</b>", body_right),
            Paragraph("<b>Deductions Total (B)</b>", body),
            Paragraph(f"<b>{_fmt_rs_cell(record.total_deductions)}</b>", body_right),
        ]
    )

    ed_w = [cw * 0.34, cw * 0.16, cw * 0.34, cw * 0.16]
    ed_t = Table(ed_rows, colWidths=ed_w, repeatRows=1)
    ed_t.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )
    story.append(_center(ed_t))
    story.append(Spacer(1, 0))

    # --- Employer / Other / CTC / Net ---
    er_pf = _fv_any(field_values, "ER_PF", "EMPLOYER_PF")
    er_esi = _fv_any(field_values, "ER_ESI", "EMPLOYER_ESI")
    other_earn = _fv_any(field_values, "OTHER_EARN", "OTHER_EARNINGS")
    c_extra = _employer_c_total(field_values)
    a = record.total_earnings
    ctc = a + c_extra

    summ_rows = [
        [
            Paragraph("Employer's Contribution (C) — PF", body),
            Paragraph(_fmt_rs_cell(er_pf), body_right),
            Paragraph("Employer's Contribution (C) — ESI", body),
            Paragraph(_fmt_rs_cell(er_esi), body_right),
        ],
        [
            Paragraph("Other Earnings (D)", body),
            Paragraph(_fmt_rs_cell(other_earn), body_right),
            Paragraph("", body),
            Paragraph("", body),
        ],
        [
            Paragraph("<b>CTC (A+C)</b>", body),
            Paragraph(f"<b>{_fmt_rs_cell(ctc)}</b>", body_right),
            Paragraph("<b>Net Pay (A-B+D)</b>", body),
            Paragraph(f"<b>{_fmt_rs_cell(record.net_pay)}</b>", body_right),
        ],
    ]
    summ_t = Table(summ_rows, colWidths=ed_w)
    summ_t.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )
    story.append(_center(summ_t))

    # --- Remaining INFO fields (location-specific) ---
    if infos:
        story.append(Spacer(1, 0))
        info_data = [[Paragraph("<b>Additional information</b>", body), Paragraph("", body)]]
        for inf in infos:
            info_data.append(
                [
                    Paragraph(inf.field_name, body),
                    Paragraph(_fmt_display(inf, field_values.get(inf.field_code)), body_right),
                ]
            )
        it = Table(info_data, colWidths=[cw * 0.55, cw * 0.45])
        it.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 1),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ]
            )
        )
        story.append(_center(it))

    # --- Amount in words ---
    words = _net_pay_words(record.net_pay)
    if words:
        story.append(Spacer(1, 0))
        wrow = Table(
            [[Paragraph(f"<b>In Words:</b> {words}", body)]],
            colWidths=[cw],
        )
        wrow.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        story.append(_center(wrow))

    # --- Leave balance (optional) ---
    cl = snap.get("cl_balance")
    if cl is None:
        cl = _fv_any(field_values, "CL_BALANCE", "CL_BAL", "cl_balance")
    if cl is not None and str(cl).strip() != "":
        story.append(Spacer(1, 0))
        story.append(
            _center(
                Table(
                    [[Paragraph(f"<b>CL Balance:</b> {cl}", body)]],
                    colWidths=[cw],
                )
            )
        )

    story.append(Spacer(1, 1))
    footer_txt = (template.footer_text or "").strip() or getattr(settings, "PROJECT_NAME", "GTMS")
    footer_style = ParagraphStyle("PSFooter", parent=body_center, textColor=footer_color)
    story.append(Paragraph(f"<i>{footer_txt}</i>", footer_style))

    doc.build(story)
    return buf.getvalue()


def _build_fallback_raw_pdf(record) -> bytes:
    lines = [
        f"Payslip - {record.month}",
        f"Employee: {getattr(record.user, 'name', '')}",
        f"Code: {getattr(record.payroll_profile, 'employee_code', '')}",
        f"Location: {getattr(record.location, 'name', '')}",
        f"Status: {record.status}",
        "",
        f"Month Days: {_fmt_day_1(record.month_days)}  Working: {_fmt_day_1(record.working_days)}",
        f"Present: {_fmt_day_1(record.present_days)}  Half: {_fmt_day_1(record.half_days)}  Absent: {_fmt_day_1(record.absent_days)}  Paid: {_fmt_day_1(record.paid_days)}",
        "",
        f"Gross: {record.gross_salary}  Earnings: {record.total_earnings}",
        f"Deductions: {record.total_deductions}  Net: {record.net_pay}",
        "",
        "Field values:",
    ]
    fv = record.field_values or {}
    for k, v in list(fv.items())[:40]:
        lines.append(f"  {k}: {v}")

    y0 = 800
    line_height = 14
    x0 = 50
    content_parts = ["BT", "/F1 10 Tf"]
    for i, line in enumerate(lines):
        y = y0 - i * line_height
        content_parts.append(f"1 0 0 1 {x0} {y} Tm ({_pdf_escape(line)}) Tj")
    content_parts.append("ET")
    content_stream = "\n".join(content_parts).encode("latin-1", errors="replace")

    objs = []
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objs.append(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
    )
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objs.append(
        b"<< /Length "
        + str(len(content_stream)).encode("ascii")
        + b" >>\nstream\n"
        + content_stream
        + b"\nendstream"
    )

    out = BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]
    for idx, obj in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{idx} 0 obj\n".encode("ascii"))
        out.write(obj)
        out.write(b"\nendobj\n")

    xref_start = out.tell()
    out.write(f"xref\n0 {len(objs)+1}\n".encode("ascii"))
    out.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.write(f"{off:010d} 00000 n \n".encode("ascii"))

    out.write(
        (
            "trailer\n"
            f"<< /Size {len(objs)+1} /Root 1 0 R >>\n"
            "startxref\n"
            f"{xref_start}\n"
            "%%EOF"
        ).encode("ascii")
    )
    return out.getvalue()


def build_simple_payslip_pdf_bytes(record) -> bytes:
    try:
        return _build_reportlab_pdf(record)
    except Exception:
        logger.exception("ReportLab payslip PDF failed; using minimal fallback")
        return _build_fallback_raw_pdf(record)
