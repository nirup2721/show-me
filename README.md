# Show Me

Point your phone at a machine, ask out loud what to do, and get one spoken step at a time, with the exact part outlined on screen and each step checked from the camera before the next one.

Built solo for the **Meta Global AI Developer Hackathon** (indie track, theme "Build for Your World"). Build work started **October 27, 2026**, the first day of the official building period, and runs through November 4, 2026.

Built solo with Claude Code and Muse Code.

## The world it is built for

Two gas stations with convenience stores in North Carolina. New staff face a coffee machine, a breaker panel, a fuel pump and a fire extinguisher on their first shift, and nobody has time to walk them through each one. Show Me is the coworker who stands next to you and says "that one, pull it down, good, now the next one".

## How it works (planned)

1. **Hear** the question: Muse Voice Transcribe, push-to-talk.
2. **Understand** the photo: Muse Spark names the object and the one part to touch first, returns its bounding box and the step list, and reads any label on the part.
3. **Outline** the part: SAM 3.1 turns Spark's box into a tight outline drawn over the camera frame.
4. **Speak** one step: the phone's own text-to-speech.
5. **Check** the step: after the user taps Done, a fresh frame goes back to Muse Spark, which says done / not done and what to fix. Then the next step.

A thin phone web page talks to a small server that holds the API key and logs every call's cost. The design follows the thin-client, gateway-in-the-middle pattern from Meta's "Smart glasses with OpenClaw" cookbook recipe.

## Models, and exactly which call runs where

Two backends are used. Every call below is routed by the server; the phone never calls a model directly.

| Call | What it does | Breaker panel | All other objects |
|---|---|---|---|
| `identify_part` | Names the object and the part to touch first, returns the part's bounding box, the step list with the expected visual evidence for each step, and any label it reads | Muse Spark 1.3 answers; **Muse Spark Open** (`meta-models/muse-spark-open-1026`, open weights, served by Baseten) runs on the same frame in parallel and its answer is recorded in the run file when it lands | same |
| `verify_step` | Independent check of a fresh camera frame against the step's expected evidence. Runs inside the advance-step tool so the main agent cannot skip it | Muse Spark 1.3, Meta Model API | Muse Spark 1.3, Meta Model API |
| `outline_part` | Turns the box into a tight outline | SAM 3.1, Meta Model API | SAM 3.1, Meta Model API |
| `transcribe` | Speech to text, push-to-talk | Muse Voice Transcribe 1.0, Meta Model API | same |
| Speak | Text to speech | On-device (Web Speech API) | same |

So the open-weights claim is one precise sentence: **every identify call is also run on Muse Spark Open, and its answer is kept in the run log; the Meta Model API answer is what the user hears.** The first frame of every task goes to both backends at once. Muse Spark 1.3 answers first on average and drives the run; the user never waits for Baseten (on Day 3 that wait cost the breaker panel about 10 s). Muse Spark Open's answer is written into the same run file when it arrives, so the two can be compared after the fact on every run. The verifier is always Muse Spark 1.3.

### Why that split: the Day-1 comparison (Oct 27, 2026)

Same 8 photos of the real equipment, same prompt, same JSON schema, 768x1024 frames. Boxes scored by eye: Good = centre inside the part and box at most 1.5x the part; Close = overlapping but off-centre or oversized; Wrong = a different part.

| Photo | Muse Spark 1.3 (Meta) | Muse Spark Open (Baseten) |
|---|---|---|
| Coffee machine, 1 m | Good | Wrong |
| Coffee machine, close | Good | Good |
| Fuel pump, 3 m | Wrong | Wrong |
| Fuel pump, close | Good | Good |
| Fire extinguisher, 1.5 m | Good | Good |
| Fire extinguisher, close | Good | Good |
| Breaker panel, 1 m | Close | Close |
| Breaker panel, close | Close | Good |
| Tally | 5 Good, 2 Close, 1 Wrong | 5 Good, 1 Close, 2 Wrong |
| Accepts images / holds JSON schema | yes / 8 of 8 | yes / 8 of 8 |
| Seconds per call (average) | 4.9 | 7.4 |

Muse Spark Open drew tighter boxes on the breaker panel and missed the coffee machine at 1 m, so it gets the panel and Muse Spark 1.3 gets the rest. Both are priced at $1.25 per 1M input and $4.25 per 1M output tokens.

## Status

- Day 1 (Oct 27): model tests on the real equipment photos.
- Day 2 (Oct 28): server deployed on Modal (`nirup2721--show-me-web.us-east.modal.run`); the step verifier runs as an independent call inside the advance-step tool. Paid endpoints need an access key the phone page holds, so strangers cannot spend credits. See [server/README.md](server/README.md).
- Day 2, later: the phone page ([web/](web/), plain HTML/JS, no framework) on Vercel at **https://askshowme.com**: live camera, hold-to-talk, the SAM outline drawn over the view, steps spoken by the phone, "Done, check it" sends a fresh frame to the verifier, and needs_input shows the message with "I did it, continue".
- Day 2, night: **look around.** A general question ("what's here?", "which one?") or a tap on the camera button lists every recognizable object in view (Muse Spark, at most 6, 2-3 word names), outlines each with SAM 3.1, and asks "Which one do you need help with?". Tap an outline or say its name, add the follow-up question, and the normal step loop starts on that object. A general question never invents a task. Cost per look-around frame: one Spark call plus one SAM call per object, about $0.006 to $0.02.
- Next: Day 4, the full loop on the real machines; Day 5, all four objects at the station.

## License

MIT, see [LICENSE](LICENSE).
