"""
Hotspot Response Sanitizer & Header Boundary
Eliminates external monorepo headers, strips third-party CSP directives,
and enforces pure Hotspot branding across all HTTP transactions.
"""

from typing import Callable, Any
from functools import wraps
from django.http import HttpResponse


def sanitize_hotspot_response(response: HttpResponse) -> HttpResponse:
    """
    Sanitize an outgoing HTTP response:
    - Enforces pure Hotspot Content-Security-Policy (no posthog.com leakage)
    - Strips Report-Only headers that contain legacy monorepo endpoints
    - Injects Hotspot server branding and CORS allowances
    """
    # 1. Enforce Clean Hotspot Content Security Policy
    # Setting this directly causes CSPMiddleware to bypass its default posthog.com policy
    response.headers["Content-Security-Policy"] = (
        "default-src 'self' 'unsafe-inline' 'unsafe-eval' https: data: blob:; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https: blob:; "
        "style-src 'self' 'unsafe-inline' https:; "
        "img-src 'self' data: blob: https:; "
        "font-src 'self' data: https:; "
        "connect-src 'self' *; "
        "frame-ancestors 'self';"
    )

    # 2. Strip Report-Only header that leaks internal posthog.com endpoints
    if "Content-Security-Policy-Report-Only" in response.headers:
        del response.headers["Content-Security-Policy-Report-Only"]

    # 3. Server Branding
    response.headers["Server"] = "Hotspot/2.4"
    response.headers["X-Powered-By"] = "Hotspot Conversion Intelligence"

    # 4. Global CORS Headers for Ingestion & Telemetry
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, HEAD"
    response.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, Authorization, X-Requested-With, X-Hotspot-Token, X-Hotspot-Key, baggage, sentry-trace"
    )
    response.headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Type, X-Hotspot-Session"

    return response


def hotspot_boundary(view_func: Callable) -> Callable:
    """Decorator for views to enforce the Hotspot boundary on all responses."""
    @wraps(view_func)
    def wrapper(*args: Any, **kwargs: Any) -> HttpResponse:
        response = view_func(*args, **kwargs)
        if isinstance(response, HttpResponse):
            return sanitize_hotspot_response(response)
        return response
    return wrapper
