from django.core.management.base import BaseCommand
from dashboard.models import AttendanceCheckin, CheckInLog
from datetime import timedelta

class Command(BaseCommand):
    help = 'Backfill site field in AttendanceCheckin from associated CheckInLog entries'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Dry run without saving changes')

    def handle(self, *args, **options):
        dry_run = options.get('dry_run')
        
        # We filter for AttendanceCheckin records where site is not yet set
        qs = AttendanceCheckin.objects.filter(site__isnull=True).select_related('guard', 'shift', 'org_location')
        total = qs.count()
        self.stdout.write(f"Evaluating {total} AttendanceCheckin records for backfill...")

        updated_count = 0
        not_found_count = 0

        for att in qs:
            # Find associated logs.
            # Match criteria: guard, shift, location, and assignment.
            # Proximity: CheckInLog timestamp should match shift_date (or next day for overnight).
            log_filters = {
                'guard': att.guard,
                'shift': att.shift,
                'org_location': att.org_location,
                'site__isnull': False
            }
            if att.assignment:
                log_filters['assignment'] = att.assignment
            
            logs = CheckInLog.objects.filter(**log_filters)
            
            if att.shift_date:
                # Look for logs on the shift_date or next day (overnight shifts)
                logs = logs.filter(
                    timestamp__date__range=[att.shift_date, att.shift_date + timedelta(days=1)]
                )
            
            # Prefer checkin logs, but any log will do to determine the site
            best_log = logs.filter(type='checkin').first() or logs.first()
            
            if best_log:
                if not dry_run:
                    att.site = best_log.site
                    att.save(update_fields=['site'])
                updated_count += 1
            else:
                not_found_count += 1
                
        if dry_run:
            self.stdout.write(self.style.SUCCESS(f"[DRY RUN] Would have updated {updated_count} records. {not_found_count} records had no matching log site."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Successfully updated {updated_count} records. {not_found_count} records could not be matched with a log site."))
