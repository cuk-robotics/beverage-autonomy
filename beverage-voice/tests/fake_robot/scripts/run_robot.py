"""테스트용 가짜 run_robot.py. docs/protocol.md 규격대로 BEVERAGE 이벤트를 찍고 stdin의 stop / task를 받는다.

--tasks가 있으면 task_list 프로토콜, --task만 있으면 옛 single infer를 흉내 낸다. 모터는 없다.
task 하나가 "끝나는" 데 걸리는 시간은 환경 변수 FAKE_ROBOT_TASK_S(기본 0.3초)다.
"""
import argparse
import json
import os
import queue
import signal
import sys
import threading
import time

SLICE_S = 0.02
TASK_S = float(os.environ.get("FAKE_ROBOT_TASK_S", "0.3"))


def emit(event, **fields):
    print("BEVERAGE " + json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


parser = argparse.ArgumentParser()
parser.add_argument("mode")
for flag in ("--policy-path", "--dataset-root", "--device", "--wrist-fourcc", "--fps", "--tasks", "--task-durations",
             "--task", "--duration-s", "--end-detector"):
    parser.add_argument(flag)
for flag in ("--validate-only", "--no-target-limit", "--return-to-initial-position"):
    parser.add_argument(flag, action="store_true")
args, extra = parser.parse_known_args()
if args.mode == "home":  # 팔을 초기 자세로 보내고 끝난다
    print("fake robot: homing", flush=True)
    time.sleep(TASK_S)
    print("fake robot: homed", flush=True)
    sys.exit(0)
if args.mode != "infer":
    sys.exit(f"fake robot only knows infer and home, got {args.mode}")
if "--boom" in extra:  # 오류 경로 시험용
    print("fake robot: boom", file=sys.stderr, flush=True)
    sys.exit(3)
if args.validate_only:
    print("fake robot: validated", flush=True)
    sys.exit(0)

if args.tasks:
    # 로봇 쪽 task_list.py와 같은 규칙이다. 팔이 종료 자세에 닿는 데 TASK_S가 걸린다고 친다.
    # --end-detector가 있으면 그때 detector로 끝나고, 상한이 먼저 오면 timeout으로 멈춘다.
    # 없으면 상한마다 다음으로 넘어간다(시험이 빨리 끝나게 상한은 TASK_S로 자른다).
    commands = queue.Queue()

    def read_stdin():
        for line in sys.stdin:
            command = line.strip()
            if command == "stop" or command.startswith("task "):
                commands.put(command)
            elif command:
                print(f"Ignored command: {command}", file=sys.stderr, flush=True)
        commands.put("stop")  # 부모가 stdin을 닫으면 멈춘다
    threading.Thread(target=read_stdin, daemon=True).start()

    plan = list(zip(json.loads(args.tasks), json.loads(args.task_durations)))
    source, index, total, outcome = "list", 0, len(plan), "complete"
    emit("ready")
    while plan:
        task, cap = plan.pop(0)
        index += 1
        emit("task_start", index=index, total=total, task=task, source=source)
        started = time.perf_counter()
        if args.end_detector:
            reason, length = ("detector", TASK_S) if TASK_S < cap else ("duration", cap)
        else:
            reason, length = "duration", min(cap, TASK_S)
        manual = None
        while time.perf_counter() - started < length:
            try:
                command = commands.get(timeout=SLICE_S)
            except queue.Empty:
                continue
            if command == "stop":
                reason = "stop"
                break
            seconds, _, sentence = command[5:].partition(" ")
            manual, reason = (sentence.strip(), float(seconds)), "preempted"
            break
        emit("task_end", index=index, reason=reason, seconds=round(time.perf_counter() - started, 2))
        if reason == "stop":
            outcome = "stop"
            break
        if reason == "duration" and args.end_detector:
            outcome = "timeout"
            break
        if reason == "preempted":
            plan, source, index, total = [manual], "manual", 0, 1
    emit("done", reason=outcome)
    time.sleep(float(os.environ.get("FAKE_ROBOT_TEARDOWN_S", "0.3")))  # 실제 로봇은 done 뒤에 복귀와 연결 해제를 한다
    sys.exit(0)

# single: 옛 infer. 윈도우에서는 로봇 쪽 패치의 SIGBREAK 처리와 같게 Ctrl+C 경로로 보낸다.
def interrupted(signum, frame):
    raise KeyboardInterrupt


if sys.platform == "win32":
    signal.signal(signal.SIGBREAK, interrupted)
print("fake robot: single task running", flush=True)  # 이 줄 뒤에 오는 신호는 Ctrl+C 경로로 처리된다
try:
    for _ in range(int(min(float(args.duration_s), TASK_S) / SLICE_S)):
        time.sleep(SLICE_S)  # 짧게 자야 신호 핸들러가 바로 돈다
    print("fake robot: task finished", flush=True)
except KeyboardInterrupt:
    print("fake robot: interrupted, returning to initial position", flush=True)
sys.exit(0)
