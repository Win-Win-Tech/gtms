from django.core.management.base import BaseCommand

from visitor.anpr.reader import run_reader_loop


class Command(BaseCommand):
    help = (
        "Run CCTV ANPR Reader (RTSP → ROI/line → track → Celery anpr queue). "
        "Requires ANPR_ENABLED=true and a Celery worker on queue 'anpr'."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process one detect cycle per camera then exit (smoke test)",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Starting ANPR Reader…"))
        run_reader_loop(once=bool(options.get("once")))
        self.stdout.write(self.style.SUCCESS("ANPR Reader stopped"))
