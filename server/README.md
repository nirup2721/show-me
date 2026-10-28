# Show Me server

A small gateway between the phone page and the Meta Model API. The phone never holds a key.

```
server/
  app.py               Modal entry point (public HTTPS, secret store, persistent volume)
  showme/
    config.py          backends, the per-role routing table, prices, caps
    costs.py           cost log (costs.jsonl) and the $100 hard stop
    models.py          the three model calls: Spark JSON, SAM outline, Voice transcribe
    prompts.py         prompts and JSON schemas
    runs.py            run state machine + one JSON file per run
    tools/             one tool per file: transcribe, identify_part, outline_part, advance_step, request_help
    verification/      verify_step: the independent check inside advance_step
    api.py             FastAPI endpoints
  tests/               offline tests with a fake model
```

## Run states

`identifying -> guiding -> verifying -> guiding ... -> done`, with `needs_input` whenever the camera cannot confirm a
step (or three fails in a row) and `failed` on a model or budget error. Each confirmed step is marked `camera` or
`human`; only all-camera runs may appear in the demo video.

## Endpoints

| Method | Path | Body | Does |
|---|---|---|---|
| GET | `/health` | | backends present, spend, tools |
| POST | `/transcribe` | `audio` file | speech to text |
| POST | `/runs` | `question`, `frame` file | identify + outline + speak step 1 |
| POST | `/runs/{id}/advance` | `frame` file | verify the current step from a fresh frame |
| POST | `/runs/{id}/confirm` | | the user's "I did it" override (logged as human-confirmed) |
| POST | `/runs/{id}/help` | `reason` | pause and say what the camera needs |
| GET | `/runs/{id}` | | the run file |
| GET | `/runs` | | recent runs with human-confirmed counts |
| GET | `/costs` | | spend by backend |

## Deploy

```
python -m modal setup                      # once, logs in
modal secret create show-me-keys META_API_KEY=... BASETEN_API_KEY=...
modal deploy server/app.py                 # prints the public URL
```
