"""OAuth provider definitions and provider-specific exchange/profile parsing."""

import hmac
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping

from ..exceptions import ExternalServiceError, ValidationError
from ..settings import settings


@dataclass(frozen=True)
class ProviderProfile:
    provider: str
    subject: str
    email: str | None
    email_verified: bool | None
    name: str | None
    avatar_url: str | None


@dataclass(frozen=True)
class OAuthProvider:
    name: str
    client_id: str
    client_secret: str
    redirect_uri: str
    server_metadata_url: str | None
    authorize_url: str | None
    access_token_url: str | None
    api_base_url: str | None
    scope: str

    @property
    def enabled(self) -> bool:
        return bool(self.client_id.strip() and self.client_secret.strip())


def get_provider(name: str) -> OAuthProvider:
    if name == "google":
        return OAuthProvider(
            name="google",
            client_id=settings.OAUTH_GOOGLE_CLIENT_ID,
            client_secret=settings.OAUTH_GOOGLE_CLIENT_SECRET,
            redirect_uri=settings.OAUTH_GOOGLE_REDIRECT_URI,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            authorize_url=None,
            access_token_url=None,
            api_base_url=None,
            scope="openid email profile",
        )
    if name == "twitch":
        return OAuthProvider(
            name="twitch",
            client_id=settings.OAUTH_TWITCH_CLIENT_ID,
            client_secret=settings.OAUTH_TWITCH_CLIENT_SECRET,
            redirect_uri=settings.OAUTH_TWITCH_REDIRECT_URI,
            server_metadata_url=None,
            authorize_url="https://id.twitch.tv/oauth2/authorize",
            access_token_url="https://id.twitch.tv/oauth2/token",
            api_base_url="https://api.twitch.tv/helix/",
            scope="user:read:email",
        )
    raise ValidationError("Unsupported OAuth provider")


def register_provider(oauth: Any, provider: OAuthProvider) -> None:
    options: dict[str, Any] = {
        "name": provider.name,
        "client_id": provider.client_id,
        "client_secret": provider.client_secret,
        "client_kwargs": {"scope": provider.scope},
    }
    if provider.server_metadata_url:
        options["server_metadata_url"] = provider.server_metadata_url
    else:
        options.update(
            authorize_url=provider.authorize_url,
            access_token_url=provider.access_token_url,
            api_base_url=provider.api_base_url,
        )
    oauth.register(**options)


def normalize_profile(provider: str, data: Mapping[str, Any]) -> ProviderProfile:
    if provider == "google":
        subject = data.get("sub")
        name = data.get("name")
        avatar_url = data.get("picture")
    elif provider == "twitch":
        subject = data.get("id")
        name = data.get("display_name") or data.get("login")
        avatar_url = data.get("profile_image_url")
    else:
        raise ValidationError("Unsupported OAuth provider")
    if not isinstance(subject, str) or not subject.strip():
        raise ValidationError("Missing user identifier from OAuth provider")
    return ProviderProfile(
        provider=provider,
        subject=subject,
        email=data.get("email") if isinstance(data.get("email"), str) else None,
        email_verified=data.get("email_verified") if isinstance(data.get("email_verified"), bool) else None,
        name=name if isinstance(name, str) else None,
        avatar_url=avatar_url if isinstance(avatar_url, str) else None,
    )


async def exchange_and_normalize_profile(
    client: Any, provider: OAuthProvider, request: Any, nonce_hash: str
) -> ProviderProfile:
    """Exchange a callback and parse its provider-specific identity payload."""
    token = await client.authorize_access_token(request)
    if provider.name == "google":
        # Authlib parses ID-token claims into ``userinfo``.  Google UserInfo
        # responses do not include the nonce, so never fall back to that
        # endpoint: accepting it would bypass ID-token nonce validation.  The
        data = token.get("userinfo")
        if not isinstance(data, Mapping):
            raise ValidationError("Missing validated OAuth identity claims")
        # Parsed claims are only trustworthy when Authlib received an ID token.
        id_token = token.get("id_token")
        if not isinstance(id_token, str) or not id_token.strip():
            raise ValidationError("Missing ID token from OAuth provider")
        nonce = data.get("nonce")
        if not isinstance(nonce, str) or not hmac.compare_digest(sha256(nonce.encode("utf-8")).hexdigest(), nonce_hash):
            raise ValidationError("Invalid OAuth nonce")
        return normalize_profile("google", data)

    if provider.name == "twitch":
        access_token = token.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ValidationError("Missing access token from OAuth provider")
        response = await client.get(
            "/users",
            token=token,
            headers={"Client-ID": provider.client_id, "Authorization": f"Bearer {access_token}"},
        )
        if not 200 <= response.status_code < 300:
            raise ExternalServiceError("Twitch", "Failed to fetch user information")
        payload = response.json()
        users = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(users, list) or not users or not isinstance(users[0], Mapping):
            raise ExternalServiceError("Twitch", "Failed to fetch user information")
        return normalize_profile("twitch", users[0])

    raise ValidationError("Unsupported OAuth provider")
