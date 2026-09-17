"""launchd entry point — the single job that runs everything.

Usage: .venv/bin/python -m app.web.serve   (waitress, 0.0.0.0:8050)

Starts, in order: the Flask app, the in-app daily scheduler, and the ngrok
tunnel supervisor. One launchd job (com.gemscraper.web) manages the lot.
"""

from waitress import serve

from app.config import WEB_HOST, WEB_PORT
from app.scheduler.inapp import InAppScheduler
from app.web.tunnel import start_tunnel
from app.web.webapp import create_app


def main():
    app = create_app()
    InAppScheduler().start()
    start_tunnel()
    print("gem-scraper web listening on %s:%d" % (WEB_HOST, WEB_PORT), flush=True)
    serve(app, host=WEB_HOST, port=WEB_PORT, threads=8)


if __name__ == "__main__":
    main()
