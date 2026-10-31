"""Closed-session PA status from worked minutes vs present_duration.

Bands (example present_duration = 8h):
  < 4h              A  Absent
  4h .. 5h          HP Half-day present   (>= present/2 and <= present/2 + 1h)
  > 5h and < 8h     LD Less duration
  >= 8h             P  Present

Open sessions stay OW / M and are not decided here.
"""

PA_STATUS_LABELS = {
    "P": "Present",
    "HP": "Half Day Present",
    "LD": "Less Duration",
    "A": "Absent",
    "OW": "On Work",
    "M": "Missed Checkout",
}


def compute_closed_pa_status(worked_minutes, required_minutes):
    """Map worked minutes to A / HP / LD / P. required_minutes is present_duration (or shift)."""
    if worked_minutes is None or required_minutes is None:
        return None
    try:
        worked = int(worked_minutes)
        required = int(required_minutes)
    except (TypeError, ValueError):
        return None
    if required <= 0:
        return "A"
    half = required // 2
    half_plus = half + 60
    if worked >= required:
        return "P"
    if worked < half:
        return "A"
    if worked <= half_plus:
        return "HP"
    return "LD"
