import json
import sys

from episode_end_detector import EpisodeEndDetector


def main():
    detector = EpisodeEndDetector.load_config()
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError("Expected a JSON object")
            command = message.get("command")
            if command == "start_episode":
                instruction = detector.start_episode(message.get("instructions"))
                result = {"status": detector.status, "current_instruction": instruction,
                          "next_instruction": None, "event": None,
                          "episode_done": False, "sequence_done": False}
            elif command == "observation":
                result = detector.update(message["state"], message.get("timestamp"))
            else:
                raise ValueError("command must be start_episode or observation")
        except (ValueError, TypeError, KeyError) as error:
            detector.reset_dwell()
            result = {"error": str(error)}
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
