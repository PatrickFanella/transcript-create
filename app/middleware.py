"""Security middleware for headers, rate limiting, and request validation."""

import gzip

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .analytics_identity import AnalyticsIdentityMiddleware
from .common.session import SESSION_COOKIE
from .logging_config import get_logger
from .settings import settings

logger = get_logger(__name__)

PRIVATE_NO_STORE = "private, no-store"
PUBLIC_CACHE_POLICIES = {
    ("GET", "/search"): "public, max-age=60, stale-while-revalidate=30",
    ("GET", "/search/grouped"): "public, max-age=60, stale-while-revalidate=30",
    ("GET", "/search/mention-map"): "public, max-age=60, stale-while-revalidate=30",
    ("GET", "/search/suggestions"): "public, max-age=30, stale-while-revalidate=15",
    ("GET", "/search/popular"): "public, max-age=300, stale-while-revalidate=60",
    ("GET", "/videos"): "public, max-age=300, stale-while-revalidate=60",
    ("GET", "/videos/{video_id}"): "public, max-age=300, stale-while-revalidate=60",
    ("GET", "/videos/{video_id}/chapters"): "public, max-age=300, stale-while-revalidate=60",
    ("GET", "/videos/{video_id}/transcript"): "public, max-age=300, stale-while-revalidate=60",
    ("GET", "/videos/{video_id}/youtube-transcript"): "public, max-age=300, stale-while-revalidate=60",
    ("GET", "/archive/summary"): "public, max-age=300, stale-while-revalidate=60",
    ("GET", "/archive/timeline"): "public, max-age=300, stale-while-revalidate=60",
    ("GET", "/archive/intelligence"): "public, max-age=300, stale-while-revalidate=60",
    ("GET", "/archive/intelligence/periods"): "public, max-age=300, stale-while-revalidate=60",
}
_CREDENTIAL_COOKIES = frozenset((SESSION_COOKIE, "tc_oauth_state"))
_PUBLIC_VARY_HEADERS = ("Cookie", "Authorization", "X-API-Key")

API_CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
    )
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware to add security headers to all responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # XSS protection (legacy but still useful for older browsers)
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Enforce HTTPS (only in production)
        if settings.ENVIRONMENT == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        # The API serves JSON rather than browser application assets. Deny all
        # resource loading by default while retaining explicit document-level
        # restrictions for any browser-rendered error response.
        response.headers["Content-Security-Policy"] = API_CONTENT_SECURITY_POLICY

        # Remove server identification
        if "server" in response.headers:
            del response.headers["server"]

        # Permissions Policy (formerly Feature Policy)
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Simple in-memory rate limiting middleware.

    For production, consider using Redis-backed rate limiting with slowapi.
    This is a basic implementation for demonstration.
    """

    def __init__(self, app):
        super().__init__(app)
        self._request_counts = {}  # Simple in-memory store
        self._last_cleanup = None

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health checks
        if request.url.path in ["/health", "/metrics"]:
            return await call_next(request)

        # Get client identifier (IP or user ID)
        client_id = self._get_client_id(request)

        # Check rate limit
        if self._is_rate_limited(client_id, request):
            logger.warning(
                "Rate limit exceeded",
                extra={
                    "client_id": client_id,
                    "path": request.url.path,
                    "method": request.method,
                },
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limit_exceeded",
                    "message": "Too many requests. Please try again later.",
                },
                headers={"Retry-After": "60"},
            )

        # Record request
        self._record_request(client_id)

        return await call_next(request)

    def _get_client_id(self, request: Request) -> str:
        """Get client identifier for rate limiting."""
        # Try to get user ID from session
        # For now, use IP address
        if request.client:
            return request.client.host
        return "unknown"

    def _is_rate_limited(self, client_id: str, request: Request) -> bool:
        """Check if client has exceeded rate limit."""
        # Simple implementation: allow 100 requests per minute
        # In production, use Redis with sliding window

        import time

        current_minute = int(time.time() / 60)
        key = f"{client_id}:{current_minute}"

        count = self._request_counts.get(key, 0)
        return count >= 100

    def _record_request(self, client_id: str):
        """Record a request for rate limiting."""
        import time

        current_minute = int(time.time() / 60)
        key = f"{client_id}:{current_minute}"

        self._request_counts[key] = self._request_counts.get(key, 0) + 1

        # Cleanup old entries periodically
        self._cleanup_old_entries()

    def _cleanup_old_entries(self):
        """Remove old rate limit entries to prevent memory leak."""
        import time

        current_minute = int(time.time() / 60)

        # Only cleanup once per minute
        if self._last_cleanup == current_minute:
            return

        self._last_cleanup = current_minute

        # Remove entries older than 2 minutes
        keys_to_delete = []
        for key in self._request_counts:
            try:
                key_minute = int(key.split(":")[-1])
                if current_minute - key_minute > 2:
                    keys_to_delete.append(key)
            except (ValueError, IndexError):
                keys_to_delete.append(key)

        for key in keys_to_delete:
            del self._request_counts[key]


class CompressionMiddleware(BaseHTTPMiddleware):
    """Middleware to compress responses using gzip."""

    MIN_SIZE = 1024  # Only compress responses > 1KB

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Check if client accepts gzip
        accept_encoding = request.headers.get("accept-encoding", "")
        if "gzip" not in accept_encoding.lower():
            return response

        # Skip compression for already compressed content
        if response.headers.get("content-encoding"):
            return response

        # Skip compression for streaming responses
        if hasattr(response, "body_iterator"):
            return response

        # Get response body
        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        # Only compress if body is large enough
        if len(body) < self.MIN_SIZE:
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

        # Compress the body
        compressed_body = gzip.compress(body, compresslevel=6)

        # Only use compressed version if it's actually smaller
        if len(compressed_body) < len(body):
            response.headers["Content-Encoding"] = "gzip"
            response.headers["Content-Length"] = str(len(compressed_body))
            return Response(
                content=compressed_body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

        # Return uncompressed if compression didn't help
        return Response(
            content=body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )


class CacheControlMiddleware(BaseHTTPMiddleware):
    """Fail closed unless a safe anonymous GET route is explicitly public."""

    @staticmethod
    def _merge_vary(response: Response) -> None:
        existing = [part.strip() for part in response.headers.get("Vary", "").split(",") if part.strip()]
        seen = {part.casefold() for part in existing}
        for header in _PUBLIC_VARY_HEADERS:
            if header.casefold() not in seen:
                existing.append(header)
                seen.add(header.casefold())
        response.headers["Vary"] = ", ".join(existing)

    @staticmethod
    def _request_has_credentials(request: Request) -> bool:
        return (
            "authorization" in request.headers
            or "x-api-key" in request.headers
            or any(cookie in request.cookies for cookie in _CREDENTIAL_COOKIES)
        )

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        downstream_policy = response.headers.get("Cache-Control", "")
        must_be_private = (
            response.status_code >= 400
            or request.method != "GET"
            or self._request_has_credentials(request)
            or "set-cookie" in response.headers
            or "no-store" in downstream_policy.casefold()
            or "private" in downstream_policy.casefold()
        )
        if must_be_private:
            response.headers["Cache-Control"] = PRIVATE_NO_STORE
            return response

        route = request.scope.get("route")
        route_template = getattr(route, "path", None)
        public_policy = PUBLIC_CACHE_POLICIES.get((request.method, route_template))
        if public_policy is None:
            response.headers["Cache-Control"] = PRIVATE_NO_STORE
            return response

        response.headers["Cache-Control"] = public_policy
        self._merge_vary(response)

        return response


def setup_session_middleware(app):
    """
    Configure session middleware for state management in OAuth flows.

    Args:
        app: FastAPI application instance
    """
    # Add session middleware for OAuth state tracking
    # Use a secure secret key from settings
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.SESSION_SECRET,
        session_cookie="tc_oauth_state",
        max_age=600,  # 10 minutes - only for OAuth flow
        same_site="lax",
        https_only=settings.ENVIRONMENT == "production",
    )


def setup_security_middleware(app):
    """
    Configure all security middleware for the application.

    Args:
        app: FastAPI application instance
    """
    # Add compression middleware
    app.add_middleware(CompressionMiddleware)

    # Add security headers
    app.add_middleware(SecurityHeadersMiddleware)

    # Add rate limiting (optional, can be disabled for development)
    if settings.ENABLE_RATE_LIMITING:
        app.add_middleware(RateLimitMiddleware)

    # Setup session middleware for OAuth
    setup_session_middleware(app)

    # Establish a pseudonymous analytics identity independently from login
    # sessions. This middleware never exposes or persists the login token.
    app.add_middleware(AnalyticsIdentityMiddleware)

    logger.info(
        "Security middleware configured",
        extra={
            "rate_limiting": settings.ENABLE_RATE_LIMITING,
            "environment": settings.ENVIRONMENT,
            "compression": True,
            "cache_control": True,
        },
    )
