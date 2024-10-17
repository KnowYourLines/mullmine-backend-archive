import logging
import os
from urllib.parse import parse_qs

import firebase_admin
from channels.auth import AuthMiddlewareStack
from channels.db import database_sync_to_async
from firebase_admin import auth, credentials

from blabhere.exceptions import InvalidFirebaseAuthToken, FirebaseAuthError
from blabhere.models import User

logger = logging.getLogger(__name__)
cred = credentials.Certificate(
    {
        "type": "service_account",
        "project_id": os.environ.get("FIREBASE_PROJECT_ID"),
        "private_key_id": os.environ.get("FIREBASE_PRIVATE_KEY_ID"),
        "private_key": os.environ.get("FIREBASE_PRIVATE_KEY").replace("\\n", "\n"),
        "client_email": os.environ.get("FIREBASE_CLIENT_EMAIL"),
        "client_id": os.environ.get("FIREBASE_CLIENT_ID"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": os.environ.get("FIREBASE_CLIENT_CERT_URL"),
        "universe_domain": "googleapis.com",
    }
)

default_app = firebase_admin.initialize_app(cred)


@database_sync_to_async
def get_user(token):
    try:
        decoded_token = auth.verify_id_token(token)
    except auth.RevokedIdTokenError as exc:
        raise InvalidFirebaseAuthToken(str(exc))
    except auth.UserDisabledError as exc:
        raise InvalidFirebaseAuthToken(str(exc))
    except auth.InvalidIdTokenError as exc:
        raise InvalidFirebaseAuthToken(str(exc))

    try:
        uid = decoded_token.get("uid")
    except Exception:
        raise FirebaseAuthError("Missing uid.")

    user, created = User.objects.get_or_create(
        username=uid,
        defaults={
            "display_name": uid,
        },
    )
    return user


class TokenAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        scope["user"] = await get_user(
            parse_qs(scope["query_string"].decode())["token"][0]
        )
        return await self.app(scope, receive, send)


def TokenAuthMiddlewareStack(app):
    return TokenAuthMiddleware(AuthMiddlewareStack(app))
