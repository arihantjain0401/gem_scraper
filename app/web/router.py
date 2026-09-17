"""Path router — one public URL, two apps (free-plan ngrok, no domain features).

Root (/) forwards to the gem website (:8050); /bento/* forwards to Bento's
Response server (:8008) with the /bento prefix stripped. ngrok points at
this router (:8060) instead of the web app directly.

The x-client-ip header (injected by ngrok's traffic_policy) is forwarded
through, so the gem login audit keeps seeing real client IPs. SSE streams
from Bento pass through chunked; socket timeouts are disabled so long
Subconscious streams are not cut mid-token.

Run: .venv/bin/python -m app.web.router  (own launchd job, KeepAlive)
"""

import urllib.error
import urllib.request

from flask import Flask, Response, request
from waitress import serve

from app.config import WEB_PORT

BENTO_PORT = 8008
ROUTER_HOST = "0.0.0.0"
ROUTER_PORT = 8060

app = Flask("gem-bento-router", static_folder=None)

# Host/content-length/connection are regenerated per hop; everything else
# (cookies, x-client-ip, content-type, accept...) passes through verbatim.
_DROP_HEADERS = {"host", "content-length", "connection", "accept-encoding"}

# Headers that must survive the hop for the client (redirects, sessions,
# auth challenges, SSE framing).
_COPY_HEADERS = {"content-type", "location", "set-cookie", "cache-control",
                 "www-authenticate", "x-accel-buffering"}

_ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Pass 3xx through untouched — the browser must follow, not the router."""

    def redirect_request(self, *args, **kwargs):
        return None


# No redirect-following, no shared cookie jar: the browser owns cookies
# end-to-end and both apps' sessions stay isolated.
_opener = urllib.request.build_opener(_NoRedirect())


def _response_from_err(err):
    out = Response(err.read(), status=err.code)
    for key, value in err.headers.items():
        if key.lower() in _COPY_HEADERS:
            out.headers[key] = value
    return out


def _forward(target_port, path):
    qs = request.query_string.decode("utf-8")
    url = "http://127.0.0.1:%d%s%s" % (target_port, path, ("?" + qs) if qs else "")
    body = request.get_data()
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _DROP_HEADERS
    }
    req = urllib.request.Request(
        url,
        data=body if request.method not in ("GET", "HEAD") else None,
        headers=headers,
        method=request.method,
    )
    try:
        resp = _opener.open(req, timeout=None)
    except urllib.error.HTTPError as err:
        return _response_from_err(err)

    def generate():
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            yield chunk

    out = Response(generate(), status=resp.status)
    for key, value in resp.headers.items():
        if key.lower() in _COPY_HEADERS:
            out.headers[key] = value
    return out


@app.route("/bento", defaults={"path": ""}, methods=_ALL_METHODS)
@app.route("/bento/", defaults={"path": ""}, methods=_ALL_METHODS)
@app.route("/bento/<path:path>", methods=_ALL_METHODS)
def bento(path):
    return _forward(BENTO_PORT, "/" + path)


@app.route("/", defaults={"path": ""}, methods=_ALL_METHODS)
@app.route("/<path:path>", methods=_ALL_METHODS)
def gem(path):
    return _forward(WEB_PORT, "/" + path)


def main():
    print("router on %s:%d -> gem :%d, /bento -> :%d"
          % (ROUTER_HOST, ROUTER_PORT, WEB_PORT, BENTO_PORT), flush=True)
    serve(app, host=ROUTER_HOST, port=ROUTER_PORT, threads=16)


if __name__ == "__main__":
    main()
