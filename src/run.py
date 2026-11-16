"""Web server start-up. Normally reached through src/chattera.py, which prepares the data folder, the configuration and
the port and then calls main() here."""

import asyncio
import json
import logging
import os
import secrets
import sys
from pathlib import Path

# Ensure the project directory is on the import path
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import chattera_i18n

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    from config_loader import apply_cli_overrides, load_config
    apply_cli_overrides()

    config_path = ROOT / "config.toml"
    if not config_path.exists():
        print(f"Error: {config_path} not found")
        sys.exit(1)

    config = load_config(ROOT)

    # --- Security: a random session token (in memory only), injected into the page for the user's own browser ---
    session_token = secrets.token_hex(32)

    from app import app, configure, set_event_loop
    configure(config, session_token=session_token)

    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import HTMLResponse

    static_dir = ROOT / "static"

    @app.get("/")
    async def index():
        # Read the page fresh each request and inject the session token. Same-origin policy keeps other sites
        # from reading the response, so only the user's own browser tab gets the token.
        # The interface language and version ride along: i18n.js loads the dictionary before any module renders.
        html = (static_dir / "mc" / "index.html").read_text("utf-8")
        boot = (f'window.__SESSION_TOKEN__="{session_token}";window.__CHATTERA_LANG__="{chattera_i18n.lang()}";'
                f'window.__CHATTERA_VERSION__={json.dumps(os.environ.get("CHATTERA_VERSION", ""))};')
        injected = html.replace("</head>", f'<script>{boot}</script>\n</head>')
        return HTMLResponse(injected, headers={"Cache-Control": "no-store"})

    class Revalidated(StaticFiles):
        """Static files the browser must revalidate before reuse (cheap 304s). ES modules import each other without a
        version in the URL, so without this a browser could keep running old code after Chattera is upgraded."""
        async def get_response(self, path, scope):
            response = await super().get_response(path, scope)
            response.headers["Cache-Control"] = "no-cache"
            return response

    app.mount("/static", Revalidated(directory=str(static_dir)), name="static")

    @app.on_event("startup")
    async def on_startup():
        # The store → WebSocket bridge needs the running loop.
        set_event_loop(asyncio.get_running_loop())

    import uvicorn
    host = config.get("server", {}).get("host", "127.0.0.1")
    port = config.get("server", {}).get("port", 8300)
    data_dir = config.get("server", {}).get("data_dir", "./data")

    # Chattera has no user authentication beyond the per-session page token: it serves only this computer.
    if host not in LOCAL_HOSTS:
        print(f"Refusing to listen on {host}: Chattera only serves this computer (127.0.0.1). "
              "It has no user accounts and must not be exposed to a network.")
        sys.exit(1)

    print("\n  Chattera")
    print(f"  Web UI:  http://{host}:{port}")
    print(f"  Data:    {data_dir}")

    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
