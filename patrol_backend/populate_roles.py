import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'patrol_backend.settings')
django.setup()

from authapp.models import Role
from scheduler.models import Location

def migrate():
    global_roles = Role.objects.filter(location__isnull=True)
    if not global_roles.exists():
        print("No global roles found to use as templates.")
        return

    locations = Location.objects.filter(is_deleted=False)
    for loc in locations:
        print(f"Syncing roles for: {loc.name}")
        for g_role in global_roles:
            role_obj, created = Role.objects.get_or_create(
                name=g_role.name,
                location=loc,
                defaults={
                    'is_default': g_role.is_default,
                    'is_allow_webapp': g_role.is_allow_webapp,
                    'is_allow_edit': g_role.is_allow_edit,
                    'is_allow_create': g_role.is_allow_create,
                    'pages': g_role.pages
                }
            )
            if created:
                print(f"  Created role: {g_role.name}")
            else:
                # Optional: Sync values if they already existed but were defaults? 
                # User wants them duplicated properly. Let's make sure values match if they are defaults.
                if role_obj.is_default:
                   role_obj.is_allow_webapp = g_role.is_allow_webapp
                   role_obj.is_allow_edit = g_role.is_allow_edit
                   role_obj.is_allow_create = g_role.is_allow_create
                   role_obj.pages = g_role.pages
                   role_obj.save()
                   print(f"  Updated existing default role: {g_role.name}")

if __name__ == "__main__":
    migrate()
