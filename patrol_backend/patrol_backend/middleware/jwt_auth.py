from channels.middleware import BaseMiddleware
from channels.db import database_sync_to_async
from django.conf import settings
from django.db import close_old_connections
from jwt import decode as jwt_decode
from urllib.parse import parse_qs

@database_sync_to_async
def get_user(user_id):
    from authapp.models import User          # ✅ lazy import
    try:
        return User.objects.get(id=user_id)
    except User.DoesNotExist:
        from django.contrib.auth.models import AnonymousUser
        return AnonymousUser()

class JWTAuthMiddleware(BaseMiddleware):

    async def __call__(self, scope, receive, send):
        close_old_connections()

        from django.contrib.auth.models import AnonymousUser  # ✅ lazy import

        scope["user"] = AnonymousUser()

        query_string = scope.get("query_string", b"").decode()
        if query_string:
            params = parse_qs(query_string)
            token_list = params.get("token")

            if token_list:
                token = token_list[0]
                try:
                    decoded = jwt_decode(
                        token,
                        settings.SECRET_KEY,
                        algorithms=["HS256"]
                    )
                    scope["user"] = await get_user(decoded["user_id"])
                except Exception:
                    scope["user"] = AnonymousUser()

        return await super().__call__(scope, receive, send)
