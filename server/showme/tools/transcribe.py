"""transcribe: push-to-talk audio -> text, with the store's equipment words as hints."""
from .. import models, prompts, runs

NAME = "transcribe"
DESCRIPTION = "Turn the user's spoken question into text."
SCHEMA = {"type": "object", "properties": {"audio": {"type": "string", "description": "bytes"},
                                           "content_type": {"type": "string"}}, "required": ["audio"]}


def run(run_record: dict | None, audio: bytes, content_type: str = "audio/wav") -> dict:
    result = models.transcribe(audio, content_type, prompts.VOICE_KEYWORDS, run_record["id"] if run_record else None)
    if run_record is not None:
        runs.event(run_record, "transcribed", text=result["text"], seconds=result["seconds"])
        runs.save(run_record)
    return result
