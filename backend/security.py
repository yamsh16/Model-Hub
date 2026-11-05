"""Response headers that make the API's replies safer to handle in a browser.

This is a plain ASGI middleware (not BaseHTTPMiddleware) so it never buffers a response: server-
sent-event streams still flow chunk by chunk. A header the route already set is left alone.
"""

DEFAULT_HEADERS = {
    # The API only ever returns JSON or an event stream: never let a browser guess it is HTML.
    b"x-content-type-options": b"nosniff",
    # Replies hold one user's private data; keep them out of shared and browser caches.
    b"cache-control": b"no-store",
    b"referrer-policy": b"no-referrer",
    b"x-frame-options": b"DENY",
    b"content-security-policy": b"default-src 'none'; frame-ancestors 'none'",
}


UNRESTRICTED_PATHS = {"/docs", "/redoc", "/docs/oauth2-redirect"}


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # The docs pages load scripts and styles from elsewhere, so the strict policy would blank them.
        if scope["type"] != "http" or scope.get("path", "") in UNRESTRICTED_PATHS:
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                present = {name.lower() for name, _ in message.get("headers", [])}
                extra = [(k, v) for k, v in DEFAULT_HEADERS.items() if k not in present]
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await self.app(scope, receive, send_with_headers)
