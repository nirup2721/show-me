"""Prompts and JSON schemas. Kept together so a backend swap cannot drift them."""

IDENTIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "object_name": {"type": "string"},
        "part_name": {"type": "string", "description": "Noun phrase of at most 4 words for the single part the user must touch first, e.g. 'Register breaker handle'. It is spoken aloud. No 'the', no dashes, no explanations."},
        "bbox": {"type": "object",
                 "properties": {"x_min": {"type": "integer"}, "y_min": {"type": "integer"},
                                "x_max": {"type": "integer"}, "y_max": {"type": "integer"}},
                 "required": ["x_min", "y_min", "x_max", "y_max"], "additionalProperties": False},
        "matched_label": {"type": "string", "description": "Exact label text read on or beside the part; empty if none."},
        "label_matches_question": {"type": "boolean"},
        "safety_warning": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "instruction": {"type": "string", "description": "One short spoken sentence."},
                    "part": {"type": "string", "description": "Noun phrase for the part this step touches."},
                    "expected_evidence": {"type": "string", "description": "One sentence a camera check could confirm from a photo taken right after the step."},
                },
                "required": ["instruction", "part", "expected_evidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["object_name", "part_name", "bbox", "matched_label", "label_matches_question", "safety_warning", "steps"],
    "additionalProperties": False,
}

IDENTIFY_PROMPT = (
    "This is a photo of equipment in a small store. The user asked: \"{question}\"\n"
    "Name the object and the ONE part the user must touch first; give that part's bounding box on a 0-1000 grid "
    "(origin top-left, x left-to-right, y top-to-bottom), tight around the part, localized precisely. Report the exact "
    "label text you read on or beside it (empty if none) and whether that label names what the user asked about. "
    "If no label names it, pick the closest part anyway but set label_matches_question to false. "
    "Give a safety warning of at most 20 words if this is electrical or fuel equipment, else an empty string. "
    "Everything you write is read aloud by a phone, so keep part_name to 4 words and every sentence short.\n"
    "Then list the steps, at most {max_steps}, one short sentence each. For EVERY step also write expected_evidence: one "
    "sentence describing what a photo taken right after that step will show if the step was done. Rules: name a specific "
    "visible thing and its state (for example 'a paper cup is standing on the drip tray under the spout', 'the screen shows "
    "the drink menu instead of the idle picture'). It must be checkable from one photo by someone who did not see the step "
    "happen. Do not write 'the user pressed the button'; write what the machine looks like afterwards. For a switch, "
    "breaker handle or lever, describe its done position relative to things in the same photo: which side of its slot it "
    "sits on, whether it lines up with the neighbouring handles, and which printed word (ON/OFF) is next to it; also say "
    "what the not-done position would look like. Prefer evidence with a large visible change (a screen, a light, a cup "
    "appearing) over a small one, and add a final step whose evidence is the outcome the user cares about."
)

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail", "cannot_tell"]},
        "evidence_seen": {"type": "string", "description": "One sentence: what the photo actually shows about the named thing."},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "evidence_seen", "reason"],
    "additionalProperties": False,
}

VERIFY_PROMPT = (
    "You are an independent checker. You see one photo taken just now. Decide whether the step below has been completed, "
    "using ONLY what is visible in this photo.\n"
    "Step: \"{instruction}\"\n"
    "Expected evidence if done: \"{evidence}\"\n"
    "Rules: answer pass only if the photo clearly shows the expected evidence. Answer fail if the photo clearly shows the "
    "relevant thing in the not-done state. Answer cannot_tell if the relevant thing is not in the photo, is blurry, is "
    "covered by a hand or object, or its state cannot be judged from this angle. A hand or finger in the photo does not by "
    "itself mean not done: if the expected evidence is clearly visible anyway, answer pass; if the hand hides the very thing "
    "you need to see, answer cannot_tell. For switches, handles and levers, do not guess from a small offset: compare the "
    "handle to its neighbours and to the printed ON/OFF marks, and if you cannot clearly see which side it sits on, answer "
    "cannot_tell rather than pass. Nobody's word counts as evidence; only the photo. "
    "First describe what you see of the named thing in one sentence, then give the verdict."
)

# Words the voice model would otherwise mishear in a noisy store. Hints only.
VOICE_KEYWORDS = ["breaker", "breaker panel", "register", "ATM", "cooler", "pilot light", "reset button", "fuel pump",
                  "nozzle", "fire extinguisher", "pin", "gauge", "coffee machine", "filter", "brew", "ice machine"]
