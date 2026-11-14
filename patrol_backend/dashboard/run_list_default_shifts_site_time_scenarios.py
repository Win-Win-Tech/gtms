"""Entrypoint: python manage.py shell -c \"from dashboard.test_list_default_shifts_site_time import run_scenarios; run_scenarios()\""""
from dashboard.test_list_default_shifts_site_time import run_scenarios

if __name__ == "__main__":
    raise SystemExit(0 if run_scenarios().failed == 0 else 1)
