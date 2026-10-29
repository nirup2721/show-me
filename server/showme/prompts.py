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
        "prep": {"type": "string", "description": "Things to have ready BEFORE step 1 that the camera cannot check, as one short sentence, e.g. 'Have a cup ready.' Empty if none."},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "instruction": {"type": "string", "description": "One short spoken sentence."},
                    "part": {"type": "string", "description": "Noun phrase for the part this step touches."},
                    "expected_evidence": {"type": "string", "description": "One sentence a camera check could confirm from a photo taken right after the step."},
                    "bbox": {"type": "object", "description": "Box around the part this step touches, 0-1000 grid of this photo. If that part is not visible, the box of the area where the change will appear.",
                             "properties": {"x_min": {"type": "integer"}, "y_min": {"type": "integer"},
                                            "x_max": {"type": "integer"}, "y_max": {"type": "integer"}},
                             "required": ["x_min", "y_min", "x_max", "y_max"], "additionalProperties": False},
                },
                "required": ["instruction", "part", "expected_evidence", "bbox"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["object_name", "part_name", "bbox", "matched_label", "label_matches_question", "safety_warning", "prep", "steps"],
    "additionalProperties": False,
}

IDENTIFY_PROMPT = (
    "This is a photo of equipment in a small store. The user asked: \"{question}\"\n{focus}"
    "Only if the question asks purely what is in view (what is this, what can you see, which one) and names nothing to do, "
    "return an empty steps list, name the object, and leave safety_warning and prep empty. Anything else is a task: "
    "'how do I use this', 'help me with this', a single word or an unclear phrase all mean 'how do I operate this "
    "machine', so produce the steps for its main everyday use.\n"
    "Name the object and the ONE part the user must touch first; give that part's bounding box on a 0-1000 grid "
    "(origin top-left, x left-to-right, y top-to-bottom), tight around the part, localized precisely. Report the exact "
    "label text you read on or beside it (empty if none) and whether that label names what the user asked about. "
    "If the question names what a breaker, switch or valve feeds (register, ATM, cooler, lights, pump), the part is the "
    "one whose printed or handwritten label names that thing, every step touches that labeled part, and matched_label is "
    "that label's text. Never guide to a differently labeled breaker. If no label in the photo names it, do not choose "
    "another one: set label_matches_question to false, put the labels you could read in matched_label, and return an "
    "empty steps list. If the question names no particular thing, label_matches_question is true. "
    "Give a safety warning of at most 20 words if this is electrical or fuel equipment, else an empty string. "
    "Everything you write is read aloud by a phone, so keep part_name to 4 words and every sentence short.\n"
    "Then list the steps, at most {max_steps}, one short sentence each (at most 10 words; every step and every "
    "evidence sentence is read aloud, and shorter answers arrive faster). Every step must be an action ON THE MACHINE in "
    "this photo whose result the camera can see from where the user is standing. Step 1 in particular must be "
    "verifiable in this very frame (for a touchscreen machine, step 1 is tapping the screen to wake the menu, not "
    "fetching a cup). Anything the user should fetch or have ready that the camera cannot check (a cup, a key, a card) "
    "goes in prep as one short sentence, e.g. 'Have a cup ready.', not in the steps. For EVERY step also write expected_evidence: one "
    "sentence of at most 20 words describing what a photo taken right after that step will show if the step was done. Rules: name a specific "
    "visible thing and its state (for example 'a paper cup is standing on the drip tray under the spout', 'the screen shows "
    "the drink menu instead of the idle picture'). It must be checkable from one photo by someone who did not see the step "
    "happen. Do not write 'the user pressed the button'; write what the machine looks like afterwards. For a switch, "
    "breaker handle or lever, describe its done position relative to things in the same photo: which side of its slot it "
    "sits on, whether it lines up with the neighbouring handles, and which printed word (ON/OFF) is next to it; also say "
    "what the not-done position would look like. Prefer evidence with a large visible change (a screen, a light, a cup "
    "appearing) over a small one, and add a final step whose evidence is the outcome the user cares about. For every "
    "step also give bbox: a tight box (0-1000 grid of this photo) around the part that step touches, or around where its "
    "change will show; the camera check looks closely inside that box."
)

# Appended to IDENTIFY_PROMPT for the retry when the planner returned no steps for a question that is not look-around.
IDENTIFY_FORCE_LINE = (
    "\nThe user wants to operate this machine. This IS a task, not a look-around question: the steps list must not be "
    "empty. If the question is unclear, give the steps for the machine's main everyday use."
)

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail", "cannot_tell", "in_progress"]},
        "evidence_seen": {"type": "string", "description": "One sentence: what the photo actually shows about the named thing."},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "evidence_seen", "reason"],
    "additionalProperties": False,
}

VERIFY_PAIR_SCHEMA = {
    "type": "object",
    "properties": {
        "change": {"type": "string", "description": "One sentence: what is different between the BEFORE and AFTER close-ups of the part, or 'no visible change'."},
        "verdict": {"type": "string", "enum": ["pass", "fail", "cannot_tell", "in_progress"]},
        "evidence_seen": {"type": "string", "description": "One sentence: the state of the named thing in the AFTER image."},
        "reason": {"type": "string"},
    },
    "required": ["change", "verdict", "evidence_seen", "reason"],
    "additionalProperties": False,
}

VERIFY_PAIR_PROMPT = (
    "You are an independent checker. Image 1 is a close-up of the part BEFORE the user was told the step. Image 2 is the "
    "same close-up NOW. Image 3 is the whole view now, small, for context only. Decide whether the step has been "
    "completed, using only what is visible.\n"
    "Step: \"{instruction}\"\n"
    "Expected evidence if done: \"{evidence}\"\n"
    "Judge the AFTER image (image 2) first: does it clearly show the expected evidence? If yes, answer pass, even if "
    "nothing changed since image 1 (the step may have been done already). If image 2 clearly shows the not-done state, "
    "answer fail. Answer cannot_tell if the part is hidden, blurry, out of the close-up, or its state cannot be judged. "
    "Then describe what changed between image 1 and image 2 (a lever, handle, button, light, screen or object moving, "
    "appearing or changing state), or 'no visible change'; use the change only as supporting evidence for a state that "
    "is hard to read, never as the reason to fail a step whose done state is already visible. A hand or finger does not "
    "by itself mean not done. Answer in_progress when a hand is on the part and the action is clearly under way (gripping or pulling a handle, pressing a button, pouring) but the done state is not visible yet; in_progress is not a failure. "
    "For switches and handles, read the handle's position against the printed marks and its "
    "neighbours in both images; do not guess from a small offset. Nobody's word counts as evidence."
)

VERIFY_PROMPT = (
    "You are an independent checker. You see one photo taken just now. Decide whether the step below has been completed, "
    "using ONLY what is visible in this photo.\n"
    "Step: \"{instruction}\"\n"
    "Expected evidence if done: \"{evidence}\"\n"
    "Rules: answer pass only if the photo clearly shows the expected evidence. Answer fail if the photo clearly shows the "
    "relevant thing in the not-done state. Answer cannot_tell if the relevant thing is not in the photo, is blurry, is "
    "covered by a hand or object, or its state cannot be judged from this angle. A hand or finger in the photo does not by "
    "itself mean not done: if the expected evidence is clearly visible anyway, answer pass; if the hand hides the very thing "
    "you need to see, answer cannot_tell. Answer in_progress when a hand is on the part and the action is clearly under way (gripping or pulling a handle, pressing a button, pouring) but the done state is not visible yet; in_progress is not a failure. "
    "For switches, handles and levers, do not guess from a small offset: compare the "
    "handle to its neighbours and to the printed ON/OFF marks, and if you cannot clearly see which side it sits on, answer "
    "cannot_tell rather than pass. Nobody's word counts as evidence; only the photo. "
    "First describe what you see of the named thing in one sentence, then give the verdict."
)

SURVEY_SCHEMA = {
    "type": "object",
    "properties": {
        "scene": {"type": "string", "description": "Where this seems to be, in at most 6 words, e.g. 'convenience store counter'."},
        "objects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "2-3 word name a person would say, e.g. 'coffee machine', 'breaker panel'."},
                    "bbox": {"type": "object",
                             "properties": {"x_min": {"type": "integer"}, "y_min": {"type": "integer"},
                                            "x_max": {"type": "integer"}, "y_max": {"type": "integer"}},
                             "required": ["x_min", "y_min", "x_max", "y_max"], "additionalProperties": False},
                },
                "required": ["name", "bbox"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["scene", "objects"],
    "additionalProperties": False,
}

SURVEY_PROMPT = (
    "Look around this photo. List every recognizable object, machine, appliance, fixture or control a person might "
    "need help using, at most {max_objects}, biggest or most useful first. For each give a 2-3 word name and a tight "
    "bounding box on a 0-1000 grid (origin top-left). Do NOT include people, hands, walls, floors or decoration. "
    "Do not describe steps or tasks; this is only a survey of what is in view."
)

# Questions with no task in them. The page and the server both treat these as "look around", never as a task.
GENERAL_QUESTION = (
    r"^\s*(what('s| is| do you see| can you see| are these| is here| is this| is that| do we have)|which one|look around|"
    r"what('s| is) (here|around|in front)|show me (what|everything)|what do you see)\b"
)

# Words the voice model would otherwise mishear in a noisy store. Hints only.
# A question that names what a breaker feeds. With a breaker panel, a planner answer whose label does not match such a
# question is refused instead of guiding to another breaker (station, Oct 29).
NAMED_LOAD = (r"\b(register|registers|atm|cooler|coolers|freezer|fridge|pump|pumps|light|lights|sign|canopy|ice|coffee|slush|slushie|"
              r"compressor|heater|hvac|cash|pos|dispenser|tv|camera|cameras|kitchen|fryer|grill|microwave|oven|outlet|outlets)\b")

VOICE_KEYWORDS = ["breaker", "breaker panel", "register", "ATM", "cooler", "pilot light", "reset button", "fuel pump",
                  "nozzle", "fire extinguisher", "pin", "gauge", "coffee machine", "filter", "brew", "ice machine"]
