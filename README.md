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

## Models

| Job | Model |
|---|---|
| Speech to text | Muse Voice Transcribe 1.0 (Meta Model API) |
| Vision, reasoning, step checking | Muse Spark 1.3 (Meta Model API) |
| Segmentation | SAM 3.1 (Meta Model API) |
| Text to speech | On-device (Web Speech API) |

## Status

Day 1: model tests. No app code yet. See the commit history for progress.

## License

MIT, see [LICENSE](LICENSE).
