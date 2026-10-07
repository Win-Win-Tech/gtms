from django.core.management.base import BaseCommand

from dashboard.cctv_face.reader import run_face_reader_loop


class Command(BaseCommand):
    help = (
        "Run CCTV face reader (Face cameras with face attendance → find and follow faces). "
        "Requires FACE_CCTV_ENABLED=true."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process one frame per camera then exit (smoke test)",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Starting face reader…"))
        run_face_reader_loop(once=bool(options.get("once")))
        self.stdout.write(self.style.SUCCESS("Face reader stopped"))
