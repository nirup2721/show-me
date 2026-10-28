"""Show Me server: a small gateway between the phone and the Meta Model API.

Layout (one tool per file, verification in its own folder, runs saved after every step):
  config.py        routes, prices, caps
  costs.py         cost log and the hard stop
  models.py        the three model calls (Spark JSON, SAM outline, Voice transcribe)
  runs.py          run state machine and storage
  tools/           transcribe, identify_part, outline_part, advance_step, request_help
  verification/    verify_step: the independent check that advance_step cannot skip
  api.py           FastAPI endpoints the phone calls
"""
