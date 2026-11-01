import json
import math
from pathlib import Path
import time

DEFAULT_HOLD_SECONDS = 2.0


def _joint_values(values):
    if isinstance(values, (str, bytes)):
        raise ValueError("Expected a flat vector of 12 finite joint values")
    values = tuple(values)
    if len(values) != 12 or any(hasattr(value, "__len__") for value in values):
        raise ValueError("Expected a flat vector of 12 finite joint values")
    values = tuple(float(value) for value in values)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Expected a flat vector of 12 finite joint values")
    return values


class EpisodeEndDetector:
    def __init__(self, model, hold_seconds=DEFAULT_HOLD_SECONDS, max_gap_seconds=0.2,
                 instructions=None):
        if model.get("version") != 1 or model.get("feature") != "observation.state":
            raise ValueError("Unsupported episode-end model")
        self.names = list(model["joint_names"])
        self.lower = _joint_values(model["lower"])
        self.upper = _joint_values(model["upper"])
        if (len(self.names) != 12 or len(set(self.names)) != 12
                or any(lower > upper for lower, upper in zip(self.lower, self.upper))):
            raise ValueError("Expected 12 ordered joints with finite lower <= upper bounds")
        if not all(math.isfinite(v) and v > 0 for v in (hold_seconds, max_gap_seconds)):
            raise ValueError("Timing settings must be finite and positive")
        self.hold_seconds = hold_seconds
        self.max_gap_seconds = max_gap_seconds
        self.status = "waiting_start"
        self._last_time = None
        self._entered_at = None
        self.instructions = self._validate_instructions(instructions)
        self._instruction_index = 0

    @staticmethod
    def _validate_instructions(instructions):
        if instructions is None:
            return ()
        if (not isinstance(instructions, (list, tuple)) or not instructions
                or any(not isinstance(text, str) or not text.strip() for text in instructions)):
            raise ValueError("instructions must be a nonempty list of nonempty strings")
        return tuple(instructions)

    @property
    def current_instruction(self):
        if self._instruction_index < len(self.instructions):
            return self.instructions[self._instruction_index]
        return None

    @property
    def sequence_done(self):
        return bool(self.instructions) and self._instruction_index >= len(self.instructions)

    @classmethod
    def load(cls, path, **kwargs):
        return cls(json.loads(Path(path).read_text(encoding="utf-8")), **kwargs)

    @classmethod
    def load_config(cls, path=None, instructions=None):
        path = Path(path) if path is not None else Path(__file__).with_name("config.json")
        config = json.loads(path.read_text(encoding="utf-8"))
        if instructions is None and "instructions_file" in config:
            instructions = json.loads((path.parent / config["instructions_file"]).read_text(encoding="utf-8"))
        return cls.load(path.parent / config["range_file"],
                        hold_seconds=config["hold_seconds"],
                        max_gap_seconds=config["max_gap_seconds"], instructions=instructions)

    def start_episode(self, instructions=None):
        if instructions is not None:
            self.instructions = self._validate_instructions(instructions)
        self._instruction_index = 0
        self._reset_episode()
        return self.current_instruction

    def _reset_episode(self):
        self.status = "waiting_exit"
        self._last_time = None
        self._entered_at = None

    def reset_dwell(self):
        self._entered_at = None

    def update(self, state, timestamp=None):
        if self.sequence_done:
            return {"status": "done", "in_range": None, "dwell_seconds": self.hold_seconds,
                    "episode_done": True, "event": None, "current_instruction": None,
                    "next_instruction": None, "sequence_done": True}
        try:
            values = _joint_values(state)
            now = time.monotonic() if timestamp is None else float(timestamp)
            if not math.isfinite(now):
                raise ValueError("timestamp must be finite")
            if self._last_time is not None and now < self._last_time:
                raise ValueError("timestamp must not go backwards")
        except (ValueError, TypeError, OverflowError) as error:
            self._entered_at = None
            raise ValueError(str(error)) from error

        inside = all(lower <= value <= upper
                     for value, lower, upper in zip(values, self.lower, self.upper))
        if self._last_time is not None and now - self._last_time > self.max_gap_seconds + 1e-9:
            self._entered_at = None
        self._last_time = now
        event = None
        next_instruction = None
        if self.status == "waiting_exit" and not inside:
            self.status = "active"
        if self.status == "active":
            if not inside:
                self._entered_at = None
            elif self._entered_at is None:
                self._entered_at = now
            if self._entered_at is not None and now - self._entered_at >= self.hold_seconds - 1e-9:
                self.status = "done"
                event = "episode_end"
        dwell = 0.0 if self._entered_at is None else max(0.0, now - self._entered_at)
        if self.status == "done":
            dwell = self.hold_seconds
        result = {"status": self.status, "in_range": inside, "dwell_seconds": dwell,
                  "episode_done": self.status == "done", "event": event}
        if event and self.instructions:
            self._instruction_index += 1
            next_instruction = self.current_instruction
            if next_instruction is not None:
                self._reset_episode()
                self._last_time = now
        result.update(current_instruction=self.current_instruction,
                      next_instruction=next_instruction, sequence_done=self.sequence_done)
        return result
