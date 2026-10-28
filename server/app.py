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


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("show-me-keys")],
    volumes={"/data": data},
    timeout=120,
    scaledown_window=300,
)
@modal.concurrent(max_inputs=8)
@modal.asgi_app()
def web():
    import os
    os.environ.setdefault("SHOWME_DATA", "/data")
    from showme.api import app as fastapi_app

    # Commit the volume after each request so run files and costs persist across containers.
    @fastapi_app.middleware("http")
    async def _commit_volume(request, call_next):
        response = await call_next(request)
        if request.method == "POST":
            try:
                data.commit()
            except Exception:
                pass
        return response

    return fastapi_app
