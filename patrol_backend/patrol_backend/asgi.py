# import os
# from django.core.asgi import get_asgi_application
# from channels.routing import ProtocolTypeRouter, URLRouter
# from patrol_backend.middleware.jwt_auth import JWTAuthMiddleware
# from livetracking.routing import websocket_urlpatterns

# os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'patrol_backend.settings')

# application = ProtocolTypeRouter({
#     "http": get_asgi_application(),
#     "websocket": JWTAuthMiddleware(
#         URLRouter(websocket_urlpatterns)
#     ),
# })


import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "patrol_backend.settings")

from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from patrol_backend.middleware.jwt_auth import JWTAuthMiddleware

# Initialize Django first
django_asgi_app = get_asgi_application()  # ✅ MUST be first

# Now import routing after Django is ready
from livetracking.routing import websocket_urlpatterns

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": JWTAuthMiddleware(
        URLRouter(websocket_urlpatterns)
    ),
})
