import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gtms.settings")
django.setup()

from django.test import Client
from users.models import User

client = Client()

# Get a valid superadmin to test with
superadmin = User.objects.filter(role__in=['superadmin', 'super_admin']).first()
if superadmin:
    client.force_login(superadmin)
    
    # Get a guard
    guard = User.objects.filter(role='guard').first()
    if guard:
        response = client.get(f'/livetracking/history/?user_id={guard.id}&timeframe=24h')
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                print(f"Success! Returned a flat list with {len(data)} items.")
            else:
                print("Failed: Did not return a flat list.")
                print(data)
        else:
            print(response.content)
    else:
        print("No guard found")
else:
    print("No superadmin found")
