"""Show Me server entry point.

Modal (public HTTPS):   modal deploy server/app.py      -> prints the web URL
Local (for tests):      uvicorn showme.api:app --app-dir server --port 8000   (reads server/.env)

The Meta and Baseten keys live in the Modal secret "show-me-keys" and never in this repo.
Run files and the cost log live on a Modal Volume mounted at /data, so the hard stop survives restarts.
"""
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install("fastapi>=0.115", "python-multipart>=0.0.9", "requests>=2.31", "openai>=1.50",
                 "Pillow>=10.0", "meta-sam-parser>=0.1.0")
    .add_local_dir(str(HERE / "showme"), remote_path="/root/showme")
)

app = modal.App("show-me")
data = modal.Volume.from_name("show-me-data", create_if_missing=True)

# askshowme.com is attached once the domain is registered in the Modal workspace (Settings > Custom Domains)
# and its DNS record points at Modal. Until then deploying with it fails ("Registered domain ... not found").
import os as _os
CUSTOM_DOMAINS = ["askshowme.com"] if _os.environ.get("SHOWME_DOMAIN", "0") == "1" else []


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("show-me-keys")],
    volumes={"/data": data},
    timeout=120,
    scaledown_window=300,
    min_containers=1,   # keep one container warm so the first request of a demo is not a cold start
)
@modal.concurrent(max_inputs=8)
@modal.asgi_app(custom_domains=CUSTOM_DOMAINS)
def web():
    import os
    # Fast local working copy; the volume at /data is the durable mirror (see showme/store.py).
    os.environ.setdefault("SHOWME_DATA", "/root/showme-data")
    os.environ.setdefault("SHOWME_VOLUME", "/data")
    from showme import store
    from showme.api import app as fastapi_app

    store.start("/data", data.commit)
    return fastapi_app
