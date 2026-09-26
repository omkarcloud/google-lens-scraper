"""Start the Google Lens Scraper API.

    python run.py            # http://localhost:8000
    PORT=9000 python run.py  # another port

Then:  curl "http://localhost:8000/text?image=https://s3-us-west-2.amazonaws.com/courses-images/wp-content/uploads/sites/1844/2017/06/15212914/images-textwrap-square.png"
"""
import bottle
from cheroot import wsgi

import config
import routes  # noqa: F401  (mounts the routes on bottle's default app)

EXAMPLE = "https://upload.wikimedia.org/wikipedia/commons/8/8d/Golden_Gate_Bridge%2C_North_view.jpg"


def main():
    app = bottle.default_app()
    print(f"Google Lens Scraper listening on http://localhost:{config.PORT}/")
    print(f"Try:  curl \"http://localhost:{config.PORT}/search?image={EXAMPLE}\"")
    if not config.CAPSOLVER_API_KEY:
        print("Tip:  set CAPSOLVER_API_KEY to solve Google's captcha automatically when it shows up "
              "(see config.py).")
    server = wsgi.Server(("0.0.0.0", config.PORT), app, server_name="google-lens-scraper", numthreads=16)
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
