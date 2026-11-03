"""run_robot.py 호출부.

options 이름은 로봇 저장소 teleoperation/cli.py와 같다. 한 번의 실행은 Session이 맡는다. plan은
(task 문장, 상한 초) 목록이고, Session이 infer 프로세스를 띄워 docs/protocol.md 규격의 BEVERAGE 이벤트를
on_event로 올린다. 중지와 끼어들기도 Session이 받는다. mode="home"이면 infer 대신 `run_robot.py home`을
띄워 팔을 초기 자세로 보낸다.

로봇 쪽 프로토콜은 둘이다.
  task_list  기본. 프로세스 하나가 리스트 전체를 돌고 stdin으로 stop / task를 받는다.
  single     옛 infer. task마다 프로세스를 새로 띄우고 이벤트는 여기서 만들어 낸다. 중지는 CTRL_BREAK
             (윈도우, 로봇 쪽 SIGBREAK 훅이 있어야 복귀 경로를 탄다) 또는 SIGINT. 끼어들기는 지금 프로세스를
             멈춘 뒤 새 프로세스를 띄우는 식이라 정책을 다시 올린다.
"""
import collections
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from .tasks import MAX_MANUAL_DURATION_S

# 추론 중에 Hugging Face 서버에 접속하지 않게 한다. 로봇 저장소 docs/inference.md, infer_beverage.bat과 같은 설정.
# PYTHONUTF8과 PYTHONUNBUFFERED는 자식 출력을 줄 단위로 바로, 깨지지 않게 받으려는 것.
CHILD_ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
# 켜져 있을 때만 붙이는 infer 플래그. 속성 이름이 그대로 옵션 이름이 된다 (no_target_limit -> --no-target-limit).
FORWARDED_FLAGS = ("validate_only", "no_target_limit", "return_to_initial_position")
EVENT_PREFIX = "BEVERAGE "
HOME_TASK = "home"         # 상태 표시용 이름. task 문장이 아니다
STOP_GRACE_S = 20.0        # stop 뒤 복귀(3초)와 연결 해제가 끝나기를 기다리는 시간. 넘기면 강제 종료
LOAD_ALLOWANCE_S = 240.0   # 정책 로딩·워밍업·장치 연결에 주는 여유. 실물에서 잰 값보다 넉넉하게


def infer_args(options, plan):
    """run_robot.py infer 인자 목록. 이름과 뜻은 로봇 cli 옵션과 같다.

    infer_extra는 이 실행기가 모르는 옵션(--rerun-port 등)이라 손대지 않고 맨 뒤에 붙인다.
    """
    args = ["infer", "--policy-path", str(options.policy_path), "--dataset-root", str(options.dataset_root),
            "--device", options.device]
    if options.wrist_fourcc:
        args += ["--wrist-fourcc", options.wrist_fourcc]
    if options.fps is not None:
        args += ["--fps", str(options.fps)]
    args += ["--" + name.replace("_", "-") for name in FORWARDED_FLAGS if getattr(options, name)]
    if options.robot_protocol == "task_list":
        args += ["--tasks", json.dumps([task for task, _ in plan], ensure_ascii=False),
                 "--task-durations", json.dumps([seconds for _, seconds in plan])]
        if options.end_detector:
            args += ["--end-detector", str(options.end_detector)]
    else:
        (task, seconds), = plan  # single 프로토콜은 task 하나씩만 띄운다
        args += ["--task", task, "--duration-s", str(seconds)]
    return args + list(options.infer_extra)


def launcher(options):
    return [options.python, str(Path(options.project_root) / "scripts" / "run_robot.py")]


def infer_command(options, plan):
    return [*launcher(options), *infer_args(options, plan)]


def home_command(options):
    """팔을 초기 자세로 보내는 로봇 쪽 모드. docs/protocol.md "home"."""
    return [*launcher(options), "home"]


class Session:
    """실행 한 번. plan의 task들을 돌리고 이벤트를 on_event(dict)로 올린다.

    이벤트 이름과 뜻은 docs/protocol.md의 stdout 규격과 같다. 끝나면 result에 done의 reason이 남는다
    (complete, timeout, stop, error). 모든 호출은 스레드 안전하다.
    """

    def __init__(self, options, plan, source, on_event, mode="infer"):
        self.options, self.plan, self.source, self.on_event, self.mode = options, list(plan), source, on_event, mode
        self.result = None
        self.commands = []  # 띄운 명령들. 팀 화면과 dry-run 출력용
        self.stderr_tail = collections.deque(maxlen=40)  # 자식 stderr 마지막 줄들. 실패 원인을 팀 화면에 보여 준다
        self._proc = None
        self._stop = threading.Event()
        self._manual = None  # 끼어든 (task, 상한). 프로세스를 띄우기 전에 들어오면 띄운 뒤 전달한다
        self._robot_done = None  # 로봇이 보낸 done. 프로세스가 끝날 때까지 쥐고 있다
        self._extra_budget = 0.0  # 끼어들기마다 늘어나는 watchdog 여유
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._guarded_run, daemon=True)

    def start(self):
        self._thread.start()
        return self

    @property
    def alive(self):
        # done을 올렸으면 자식은 이미 끝났다. 스레드가 마무리하는 짧은 틈에 버튼이 눌려도 끝난 세션으로 본다
        return self._thread.is_alive() and self.result is None

    def wait(self, timeout=None):
        self._thread.join(timeout)
        return self.result

    def stop(self):
        """지금 task를 끝내고 정상 종료 경로로. 로봇 쪽이 복귀와 연결 해제를 한다."""
        self._stop.set()
        with self._lock:
            proc = self._proc
        if proc is None:
            return
        if self.mode == "infer" and self.options.robot_protocol == "task_list":
            self._write(proc, "stop")
        else:
            self._interrupt(proc)

    def preempt(self, task, seconds):
        """사람이 넣은 명령. 지금 task를 끝내고 남은 리스트를 버린 뒤 이 문장 하나만 돈다. home 세션은 못 받는다."""
        if self.mode != "infer":
            return False
        with self._lock:
            proc = self._proc
            self._manual = (task, seconds)
            self._extra_budget += seconds + STOP_GRACE_S  # 수동 task와 그 앞의 복귀 시간만큼 watchdog을 늦춘다
        if proc is not None:
            if self.options.robot_protocol == "task_list":
                self._write(proc, f"task {seconds:g} {task}")
            else:
                self._interrupt(proc)
        return True

    # -- 내부 --------------------------------------------------------------

    def _emit(self, event):
        if event.get("event") == "done":
            self.result = event.get("reason", "error")
        self.on_event(event)

    def _guarded_run(self):
        try:
            self._run()
        except Exception as error:  # 스레드 안에서 조용히 죽지 않게. 원인은 done 이벤트로 올린다
            self._emit({"event": "done", "reason": "error", "message": f"{type(error).__name__}: {error}"})

    def _run(self):
        if self.mode == "home":
            self._run_home()
        elif self.options.robot_protocol == "task_list":
            self._run_task_list()
        else:
            self._run_single()

    def _run_task_list(self):
        proc = self._launch(infer_command(self.options, self.plan), self.plan)
        code = self._pump(proc)
        done, self._robot_done = self._robot_done, None
        if done is not None:  # 로봇은 done 뒤에 복귀와 연결 해제를 한다. 그게 끝나 프로세스가 나간 지금 알린다
            if code == 0:
                self._emit(done)
            else:
                self._emit({"event": "done", "reason": "error", "message": self._tail(code)})
        elif self.result is None:  # done 이벤트 없이 끝났다
            if self.options.validate_only and code == 0:
                self._emit({"event": "done", "reason": "complete"})
            else:
                reason = "stop" if self._stop.is_set() else "error"
                self._emit({"event": "done", "reason": reason, "message": self._tail(code)})

    def _run_single(self):
        plan, source, index, total = list(self.plan), self.source, 0, len(self.plan)
        outcome = "complete"
        self._emit({"event": "ready"})  # 연결 시점을 알 길이 없어 미리 낸다. 로딩 중에도 화면은 "시작" 쪽으로 보인다
        while plan and not self._stop.is_set():
            task, seconds = plan.pop(0)
            index += 1
            self._emit({"event": "task_start", "index": index, "total": total, "task": task, "source": source})
            started = time.perf_counter()
            proc = self._launch(infer_command(self.options, [(task, seconds)]), [(task, seconds)])
            code = self._pump(proc)
            with self._lock:
                manual, self._manual = self._manual, None
            if self._stop.is_set():
                reason = "stop"
            elif manual:
                reason = "preempted"
            elif code == 0:
                reason = "complete" if self.options.validate_only else "duration"
            else:
                reason = "error"
            self._emit({"event": "task_end", "index": index, "reason": reason,
                        "seconds": round(time.perf_counter() - started, 1)})
            if reason == "stop":
                outcome = "stop"
                break
            if reason == "error":
                self._emit({"event": "done", "reason": "error", "message": self._tail(code)})
                return
            if reason == "preempted":
                plan, source, index, total = [manual], "manual", 0, 1
        self._emit({"event": "done", "reason": outcome})

    def _run_home(self):
        """`run_robot.py home` 한 번. 이벤트는 여기서 만든다. 로봇 쪽은 종료 코드로만 답하면 된다."""
        self._emit({"event": "ready"})
        self._emit({"event": "task_start", "index": 1, "total": 1, "task": HOME_TASK, "source": "home"})
        started = time.perf_counter()
        proc = self._launch(home_command(self.options), [])
        code = self._pump(proc)
        reason = "stop" if self._stop.is_set() else ("complete" if code == 0 else "error")
        self._emit({"event": "task_end", "index": 1, "reason": reason, "seconds": round(time.perf_counter() - started, 1)})
        if reason == "error":
            self._emit({"event": "done", "reason": "error", "message": self._tail(code)})
        else:
            self._emit({"event": "done", "reason": reason})

    def _launch(self, command, plan):
        self.commands.append(command)
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0  # CTRL_BREAK을 이 자식에게만
        proc = subprocess.Popen(command, cwd=self.options.project_root, env={**os.environ, **CHILD_ENV},
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=flags)
        threading.Thread(target=self._drain_stderr, args=(proc,), daemon=True).start()
        budget = LOAD_ALLOWANCE_S + sum(seconds for _, seconds in plan) + MAX_MANUAL_DURATION_S + STOP_GRACE_S
        threading.Thread(target=self._watchdog, args=(proc, budget), daemon=True).start()
        with self._lock:
            self._proc = proc
            stop_requested, manual = self._stop.is_set(), self._manual
        # 프로세스를 띄우기 전에 들어온 stop이나 끼어들기는 아직 전달할 곳이 없었다. 지금 전달한다.
        task_list = self.mode == "infer" and self.options.robot_protocol == "task_list"
        if stop_requested:
            self._write(proc, "stop") if task_list else self._interrupt(proc)
        elif manual is not None and self.mode == "infer":
            self._write(proc, f"task {manual[1]:g} {manual[0]}") if task_list else self._interrupt(proc)
        return proc

    def _pump(self, proc):
        """자식 stdout을 끝까지 읽는다. BEVERAGE 줄은 이벤트로 올리고 나머지는 콘솔에 그대로 찍는다."""
        for line in proc.stdout:
            if line.startswith(EVENT_PREFIX):
                try:
                    event = json.loads(line[len(EVENT_PREFIX):])
                except ValueError:
                    print(line, end="", flush=True)
                    continue
                if event.get("event") == "done":
                    event.setdefault("message", "")
                    self._robot_done = event  # 프로세스가 끝난 뒤에 올린다. 그때까지는 장치를 쥐고 있다
                    continue
                self._emit(event)
            else:
                print(line, end="", flush=True)
        code = proc.wait()
        with self._lock:
            self._proc = None
        for stream in (proc.stdin, proc.stdout):
            try:
                stream.close()
            except OSError:
                pass
        return code

    def _drain_stderr(self, proc):
        for line in proc.stderr:
            self.stderr_tail.append(line.rstrip("\n"))
            print(line, end="", file=sys.stderr, flush=True)  # 로봇 쪽 로그는 콘솔에서도 계속 보이게
        proc.stderr.close()

    def _watchdog(self, proc, budget):
        """자식이 상한을 한참 넘겨도 안 끝나면 stop을 보내고, 그래도 안 끝나면 강제 종료한다."""
        started = time.monotonic()
        while (left := budget + self._extra_budget - (time.monotonic() - started)) > 0:
            if self._wait_exit(proc, min(left, 5.0)):
                return
        if proc.poll() is not None:
            return
        print(f"robot process exceeded {budget:.0f}s; sending stop", flush=True)
        self.stop()
        if self._wait_exit(proc, STOP_GRACE_S):
            return
        print("robot process did not stop; terminating (no return to initial position)", flush=True)
        proc.terminate()

    @staticmethod
    def _wait_exit(proc, seconds):
        try:
            proc.wait(timeout=seconds)
            return True
        except subprocess.TimeoutExpired:
            return False

    @staticmethod
    def _write(proc, line):
        try:
            proc.stdin.write(line + "\n")
            proc.stdin.flush()
        except (OSError, ValueError):  # 이미 끝난 프로세스
            pass

    @staticmethod
    def _interrupt(proc):
        """Ctrl+C와 같은 경로로 끝내 달라는 신호. 윈도우는 CTRL_BREAK, 그 밖에는 SIGINT."""
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT)
        except (OSError, ValueError):
            pass

    def _tail(self, code):
        lines = list(self.stderr_tail)[-12:]
        head = f"robot process exited with code {code}" if code not in (None, 0) else "robot process ended without a done event"
        return head + ("\n" + "\n".join(lines) if lines else "")


class DryRunSession(Session):
    """로봇 없이 흐름만 본다. 명령을 출력하고 task마다 잠깐 기다린 뒤 끝난 것처럼 이벤트를 낸다."""

    task_seconds = 0.5  # task 하나가 끝난 셈 치는 시간

    def _run(self):
        if self.mode == "home":
            command = home_command(self.options)
            self.commands.append(command)
            print("  " + subprocess.list2cmdline(command), flush=True)
            plan = [(HOME_TASK, 0.0)]
        else:
            plan = list(self.plan)
            commands = [plan] if self.options.robot_protocol == "task_list" else [[step] for step in plan]
            for sub_plan in commands:
                command = infer_command(self.options, sub_plan)
                self.commands.append(command)
                print("  " + subprocess.list2cmdline(command), flush=True)
            if self.options.validate_only:
                self._emit({"event": "done", "reason": "complete"})
                return
        source, index, total = self.source, 0, len(plan)
        self._emit({"event": "ready"})
        outcome = "complete"
        while plan and not self._stop.is_set():
            task, _ = plan.pop(0)
            index += 1
            self._emit({"event": "task_start", "index": index, "total": total, "task": task, "source": source})
            started = time.perf_counter()
            reason = "detector" if self.mode == "infer" else "complete"
            while time.perf_counter() - started < self.task_seconds:
                with self._lock:
                    manual = self._manual
                if self._stop.is_set():
                    reason = "stop"
                    break
                if manual and self.mode == "infer":
                    reason = "preempted"
                    break
                time.sleep(0.02)
            self._emit({"event": "task_end", "index": index, "reason": reason,
                        "seconds": round(time.perf_counter() - started, 1)})
            if reason == "stop":
                outcome = "stop"
                break
            if reason == "preempted":
                with self._lock:
                    plan, self._manual = [self._manual], None
                source, index, total = "manual", 0, 1
        self._emit({"event": "done", "reason": outcome})


def launch(options, plan, source, on_event, mode="infer"):
    """plan을 돌리는 Session을 만들어 시작한다. --dry-run이면 로봇 없이 흉내만 낸다."""
    session_class = DryRunSession if options.dry_run else Session
    return session_class(options, plan, source, on_event, mode).start()


def validate(options, plan):
    """infer를 --validate-only로 한 번 돌려 본다. (결과, 자식 stderr 꼬리)를 돌려준다.

    가중치와 장치 없이 설정과 feature 계약만 보므로 수 초면 끝난다. 서버를 띄울 때 옵션 오타, 경로 오류,
    로봇 쪽이 아직 --tasks를 모르는 경우를 첫 주문 전에 잡으려는 것.
    """
    import copy
    checked = copy.copy(options)
    checked.validate_only = True
    checked.dry_run = False
    session = Session(checked, plan, "check", lambda event: None).start()
    result = session.wait()
    return result, "\n".join(list(session.stderr_tail)[-12:])
