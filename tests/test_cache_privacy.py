"""Route-contract tests for explicit cache privacy."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from starlette.routing import Route

from app.middleware import PUBLIC_CACHE_POLICIES, CacheControlMiddleware

PRIVATE = "private, no-store"
PUBLIC_SEARCH = "public, max-age=60, stale-while-revalidate=30"


@pytest.fixture
def cache_client() -> TestClient:
    app = FastAPI()
    app.add_middleware(CacheControlMiddleware)

    @app.get("/search")
    def public_search(
        response: Response,
        issue_cookie: bool = False,
        downstream_no_store: bool = False,
        downstream_private: bool = False,
    ):
        response.headers["Vary"] = "Accept-Encoding"
        if issue_cookie:
            response.set_cookie("credential", "secret")
        if downstream_no_store:
            response.headers["Cache-Control"] = "no-store, max-age=0"
        if downstream_private:
            response.headers["Cache-Control"] = "private, max-age=60"
        return {"hits": []}

    @app.get("/search/history")
    def search_history():
        return {"items": []}

    @app.get("/favorites")
    def favorites():
        return {"items": []}

    @app.get("/saved-searches")
    def saved_searches():
        return {"items": []}

    @app.get("/profile")
    def profile():
        return {"email": "private@example.invalid"}

    @app.post("/api-keys")
    def create_api_key():
        return {"key": "secret"}

    @app.get("/cookie")
    def set_cookie(response: Response):
        response.set_cookie("credential", "secret")
        return {"ok": True}

    @app.get("/downstream-no-store")
    def downstream_no_store(response: Response):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return {"ok": True}

    @app.get("/failure")
    def failure(response: Response):
        response.status_code = 503
        return {"error": "unavailable"}

    return TestClient(app)


def test_only_explicit_safe_anonymous_get_is_public(cache_client: TestClient) -> None:
    response = cache_client.get("/search")

    assert response.headers["cache-control"] == PUBLIC_SEARCH
    vary = {part.strip().lower() for part in response.headers["vary"].split(",")}
    assert vary == {"accept-encoding", "cookie", "authorization", "x-api-key"}


def test_cache_policy_wraps_every_application_middleware() -> None:
    from app.main import app

    assert app.user_middleware[0].cls is CacheControlMiddleware

    def registered_routes(routes):
        for route in routes:
            methods = getattr(route, "methods", None) or set()
            path = getattr(route, "path", None)
            yield from ((method, path) for method in methods if path)
            original_router = getattr(route, "original_router", None)
            if original_router is not None:
                yield from registered_routes(original_router.routes)

    registered = set(registered_routes(app.routes))
    assert set(PUBLIC_CACHE_POLICIES).issubset(registered)


def test_actual_app_unhandled_exception_is_private() -> None:
    from app.main import app

    async def raise_unhandled(_request):
        raise RuntimeError("cache privacy regression probe")

    route = Route("/__test_unhandled_cache_privacy", raise_unhandled)
    app.router.routes.append(route)
    try:
        response = TestClient(app, raise_server_exceptions=False).get(route.path)
    finally:
        app.router.routes.remove(route)

    assert response.status_code == 500
    assert response.headers["cache-control"] == PRIVATE


@pytest.mark.parametrize(
    "path",
    ["/search/history", "/favorites", "/saved-searches", "/profile", "/unknown"],
)
def test_private_gets_default_to_no_store(cache_client: TestClient, path: str) -> None:
    assert cache_client.get(path).headers["cache-control"] == PRIVATE


@pytest.mark.parametrize(
    ("headers", "cookies"),
    [
        ({"Authorization": "Bearer secret"}, {}),
        ({"X-API-Key": "secret"}, {}),
        ({}, {"tc_session": "secret"}),
        ({}, {"tc_oauth_state": "secret"}),
    ],
)
def test_credentials_force_public_route_private(
    cache_client: TestClient,
    headers: dict[str, str],
    cookies: dict[str, str],
) -> None:
    assert cache_client.get("/search", headers=headers, cookies=cookies).headers["cache-control"] == PRIVATE


def test_non_get_secret_response_is_private(cache_client: TestClient) -> None:
    assert cache_client.post("/api-keys").headers["cache-control"] == PRIVATE


@pytest.mark.parametrize(
    "query",
    ["issue_cookie=true", "downstream_no_store=true", "downstream_private=true"],
)
def test_public_route_response_privacy_overrides_allowlist(cache_client: TestClient, query: str) -> None:
    assert cache_client.get(f"/search?{query}").headers["cache-control"] == PRIVATE


@pytest.mark.parametrize("path", ["/cookie", "/downstream-no-store", "/failure"])
def test_response_credentials_errors_and_downstream_no_store_are_private(
    cache_client: TestClient,
    path: str,
) -> None:
    assert cache_client.get(path).headers["cache-control"] == PRIVATE
