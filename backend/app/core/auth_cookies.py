"""
HttpOnly auth cookies for browser sessions.

Keeps JWTs out of localStorage / document.cookie so XSS cannot exfiltrate them.
Programmatic clients can still use Authorization: Bearer or X-API-Key.
"""

from fastapi import Request, Response

from app.core.config import settings

ACCESS_COOKIE = "qi_access"
REFRESH_COOKIE = "qi_refresh"


def _is_https(request: Request) -> bool:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").lower()
    return proto == "https"


def set_auth_cookies(
    response: Response,
    request: Request,
    access_token: str,
    refresh_token: str,
) -> None:
    secure = _is_https(request)
    response.set_cookie(
        key=ACCESS_COOKIE,
        value=access_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(key=ACCESS_COOKIE, path="/")
    response.delete_cookie(key=REFRESH_COOKIE, path="/")


def get_access_token_from_request(request: Request) -> str | None:
    return request.cookies.get(ACCESS_COOKIE) or None


def get_refresh_token_from_request(request: Request) -> str | None:
    return request.cookies.get(REFRESH_COOKIE) or None
