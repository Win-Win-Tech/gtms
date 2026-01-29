from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.tokens import UntypedToken
from jwt import decode as jwt_decode
from django.conf import settings
from authapp.models import User

@database_sync_to_async
def get_user(user_id):
    try:
        return User.objects.get(id=user_id)
    except User.DoesNotExist:
        return AnonymousUser()

class JWTAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # Extract token from query string (e.g., ws://.../?token=XYZ)
        query_string = scope.get("query_string", b"").decode()
        if query_string:
            query_params = dict(qp.split('=') for qp in query_string.split('&') if '=' in qp)
            token = query_params.get('token')

            if token:
                try:
                    # Use simplejwt settings or direct jwt decode
                    decoded_data = jwt_decode(token, settings.SECRET_KEY, algorithms=["HS256"])
                    scope['user'] = await get_user(decoded_data['user_id'])
                except Exception:
                    scope['user'] = AnonymousUser()
            else:
                scope['user'] = AnonymousUser()
        else:
            scope['user'] = AnonymousUser()

        return await self.app(scope, receive, send)

