"""The three kinds of model call. Nothing in here knows about runs or steps."""
import asyncio
import base64
import io
import json
import subprocess
import time

import requests
from PIL import Image, ImageOps

from . import config, costs


# ---------- frames ----------

def frame_from_bytes(data: bytes) -> tuple[str, int, int]:
    """Downsize to FRAME_MAX_SIDE, JPEG, return (data_url, width, height). All coordinates refer to this size."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    img.thumbnail((config.FRAME_MAX_SIDE, config.FRAME_MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii"), img.width, img.height


# ---------- Spark (JSON answer from a photo) ----------

class SparkError(RuntimeError):
    pass


def spark_json(backend: str, prompt: str, data_url: str, schema: dict, schema_name: str,
               run_id: str | None = None, kind: str = "spark", max_tokens: int = 3000,
               reasoning_effort: str = "low") -> tuple[dict, dict]:
    """One Chat Completions call with a strict JSON schema. Returns (parsed_json, meta)."""
    costs.check_budget(backend)
    base = config.META_BASE_URL if backend == "meta" else config.BASETEN_BASE_URL
    body = {
        "model": config.MODELS[backend],
        "reasoning_effort": reasoning_effort,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}],
        "response_format": {"type": "json_schema", "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
    }
    t0 = time.time()
    r = requests.post(f"{base}/chat/completions", json=body, timeout=90,
                      headers={"Authorization": f"Bearer {config.key(backend)}", "Content-Type": "application/json"})
    dt = round(time.time() - t0, 2)
    if r.status_code != 200:
        raise SparkError(f"{backend} HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    usage = data.get("usage", {}) or {}
    usd = costs.log(kind, backend, costs.spark_cost(usage, backend), run_id, seconds=dt,
                    model=config.MODELS[backend], tokens_in=usage.get("prompt_tokens"), tokens_out=usage.get("completion_tokens"))
    content = data["choices"][0]["message"].get("content")
    if not content:
        raise SparkError(f"{backend} returned an empty answer (finish_reason={data['choices'][0].get('finish_reason')})")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        raise SparkError(f"{backend} answer was not JSON: {e}")
    return parsed, {"backend": backend, "model": config.MODELS[backend], "seconds": dt, "usd": usd, "usage": usage}


# ---------- SAM 3.1 (outline from a box) ----------

async def _sam_async(prompt: str, data_url: str):
    from openai import AsyncOpenAI
    import meta_sam_parser as msp

    client = AsyncOpenAI(base_url=config.META_BASE_URL, api_key=config.key("meta"))
    stream = await client.responses.create(
        model=config.SAM_MODEL, stream=True,
        metadata={"include_confidence": "true", "mask_encoding": "one_bit"},
        input=[{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": prompt},
            {"type": "input_image", "image_url": data_url},
        ]}],
    )

    async def source():
        async for ev in stream:
            yield ev

    parsed = msp.parse_responses_stream(source(), msp.image_segmentation_format())
    try:
        return await parsed.final_result()
    finally:
        await stream.close()


def sam_outline(box_px: tuple[int, int, int, int], width: int, height: int, data_url: str,
                run_id: str | None = None) -> dict | None:
    """Box prompt from Spark's pixel box. Returns {svg_path, mask_w, mask_h, bounds, box, confidence} or None if SAM found nothing."""
    import meta_sam_parser as msp

    costs.check_budget("meta")
    x1, y1, x2, y2 = box_px
    prompt = msp.build_object_prompt(size=(width, height), objects=[
        msp.PromptObject(id=1, prompts=[msp.FramePrompt(frame=0, box=msp.PromptBox(x1, y1, x2, y2))])])
    t0 = time.time()
    # Run on a worker thread with its own event loop: this is called from inside FastAPI's running loop.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(lambda: asyncio.run(_sam_async(prompt, data_url))).result(timeout=60)
    dt = round(time.time() - t0, 2)
    costs.log("sam", "meta", config.SAM_PER_IMAGE, run_id, seconds=dt)
    for rec in result.records:
        if isinstance(rec, msp.SegmentationMaskRecord):
            b = rec.bounds
            return {
                "svg_path": msp.decode_mask_to_svg_path(rec.mask),
                "mask_w": rec.mask.width, "mask_h": rec.mask.height,
                "bounds": [round(b.left), round(b.top), round(b.right), round(b.bottom)],
                "confidence": rec.confidence, "seconds": dt,
            }
    return None


# ---------- Muse Voice Transcribe (file mode) ----------

def _to_wav16k(audio: bytes, content_type: str) -> bytes:
    """Any phone recording -> 16 kHz mono 16-bit WAV, via ffmpeg (installed in the server image)."""
    if content_type == "audio/wav" and audio[:4] == b"RIFF":
        # Trust WAVs the phone page builds itself (it writes 16 kHz mono 16-bit).
        return audio
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", "-f", "wav", "pipe:1"],
                         input=audio, capture_output=True)
    if out.returncode != 0:
        raise RuntimeError("ffmpeg could not read the audio: " + out.stderr.decode(errors="replace")[:200])
    return out.stdout


def transcribe(audio: bytes, content_type: str, keywords: list[str], run_id: str | None = None) -> dict:
    costs.check_budget("meta")
    wav = _to_wav16k(audio, content_type)
    req = {"model": config.VOICE_MODEL, "audioEncoding": "WAV", "mode": "PUSH_TO_TALK", "keywords": keywords}
    t0 = time.time()
    r = requests.post(f"{config.META_BASE_URL}/asr/transcribe", timeout=60,
                      headers={"Authorization": f"Bearer {config.key('meta')}", "Accept": "application/json"},
                      files={"request": (None, json.dumps(req), "application/json"), "audio": ("q.wav", wav, "audio/wav")})
    dt = round(time.time() - t0, 2)
    if r.status_code != 200:
        raise RuntimeError(f"voice HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    ms = data.get("audioDurationMs") or 0
    costs.log("voice", "meta", ms / 3600000 * config.VOICE_PER_HOUR, run_id, seconds=dt, audio_ms=ms)
    return {"text": (data.get("transcript") or "").strip(), "audio_ms": ms, "seconds": dt}
