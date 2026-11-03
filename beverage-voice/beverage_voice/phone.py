"""phone 모드: 폰 브라우저에서 보낸 명령을 받아 infer를 부르는 작은 웹 서버.

화면은 둘이다. 시연용(/)은 관객 앞에서 쓰는 화면으로 음료 주문과 진행 상황만 있다. 팀용(/team)에는 수동
실행, 중지, 주문 받기, 실패 원인이 있다. 토큰도 따로 준다. 시연 토큰은 음료 주문(/command, /speak)과 /status만
되고, 팀 토큰은 /infer, /stop, /home, /resume, /debug까지 된다. 시연 화면 주소가 남의 눈에 띄어도 그 토큰으로는
로봇을 멈추거나 단계 하나를 골라 돌리지 못한다.

우선순위는 이렇다. 팀이 /infer로 넣은 명령이 가장 높다. 리스트가 도는 중이라도 지금 task를 끝내고 리스트를
버린 뒤 그 문장 하나만 돈다. 음료 주문은 로봇이 쉬고 있을 때(idle)만 받는다. 한 잔이 어떻게 끝났든 그 뒤는
hold로 들어가서, 팀이 작업대를 정리하고 "주문 받기"를 누르기 전까지 음료 주문을 받지 않는다.

음성은 PC가 전사 API(Groq 또는 OpenAI의 Whisper)로 받아 적는다. API 키가 폰으로 넘어가지 않게 전사는 PC에서 한다. 표준
라이브러리만 쓴다.

서버는 localhost에만 열린다. 폰에서는 `tailscale serve --bg 8000`이 내 테일넷 안에서만 HTTPS로 중계한
주소로 들어온다. 브라우저가 HTTPS 페이지에서만 마이크를 허락해서 Serve를 쓴다.
"""
import collections
import json
import os
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .inference import HOME_TASK, STOP_GRACE_S, launch, validate
from .stt import PROVIDERS, TranscribeError, transcribe
from .tasks import (DEFAULT_DURATION_S, POWDER_TASKS, STT_PROMPT, TASK_NAMES, check_manual_duration, drink_tasks, drinks_in,
                    plan, resolve_task)

BASE = Path(__file__).resolve().parents[1]
WEB = Path(__file__).resolve().parent / "web"  # 요청마다 읽으니 HTML을 고치고 새로고침만 하면 된다
LOG_PATH = BASE / "runs.jsonl"  # 실행 기록. Git에서 뺀다
PAGES = {"/": ("demo.html", "demo"), "/team": ("team.html", "team")}  # 경로: (파일, 필요한 권한)
ROLES = ("demo", "team")  # 뒤로 갈수록 권한이 크다. team은 demo가 할 수 있는 것을 다 한다
TEAM_PATHS = ("/infer", "/stop", "/resume", "/home", "/debug")
# /debug 설정 칸에 보여 줄 options. 로봇 저장소 위치는 뺐다.
DEBUG_OPTIONS = ("policy_path", "dataset_root", "duration_s", "device", "fps", "wrist_fourcc", "no_target_limit",
                 "return_to_initial_position", "validate_only", "dry_run", "stt_provider", "stt_model", "robot_protocol",
                 "end_detector", "infer_extra")


def page_tasks():
    """팀 화면 수동 실행 목록. {단계 이름: [task 문장, 기본 상한 초]}"""
    return {name: [task, DEFAULT_DURATION_S[task]] for name, task in TASK_NAMES.items()}


class Server(ThreadingHTTPServer):
    """요청은 스레드마다 따로 받지만 로봇 작업은 한 번에 하나만 돌린다."""

    daemon_threads = True

    def __init__(self, address, options, tokens, api_key):
        super().__init__(address, Handler)
        self.options, self.tokens, self.api_key = options, tokens, api_key  # tokens: {"demo": ..., "team": ...}
        self.lock = threading.Lock()
        self.session = None
        self.started_at = None
        self.home_from = None  # home을 누를 때의 phase. 주문을 받던 중이었으면 끝난 뒤 다시 받는다
        self.events = collections.deque(maxlen=60)  # 팀 화면 최근 기록. 요청과 로봇 이벤트를 같이 쌓는다
        # /status가 그대로 내보내는 값. phase: idle(주문 받음) loading(로봇 준비 중) running stopping hold(정리 중)
        self.state = {"phase": "idle", "job": 0, "drink": None, "source": None, "heard": None, "plan": [],
                      "step": 0, "steps": 0, "task": None, "ready": False, "last": None}

    # -- 권한과 기록 -------------------------------------------------------

    def role(self, token):
        """토큰에 맞는 권한 이름. 둘 다 아니면 None. compare_digest는 비교 시간이 일정해 응답 속도로 못 맞힌다."""
        for name in reversed(ROLES):
            if secrets.compare_digest((token or "").encode(), self.tokens[name].encode()):  # 바이트로 비교해야 한글 토큰도 된다
                return name
        return None

    def note(self, kind, **fields):
        self.events.append({"time": time.strftime("%H:%M:%S"), "kind": kind, **fields})

    def log(self, record):
        """실행 기록 한 줄. 평가 문서가 요구하는 성공·실패·개입 기록의 바탕이다. 성공 판정은 사람이 적는다."""
        try:
            with LOG_PATH.open("a", encoding="utf-8") as out:
                out.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), **record}, ensure_ascii=False) + "\n")
        except OSError as error:
            print(f"run log failed: {error}", flush=True)

    # -- 작업 시작·중지 ----------------------------------------------------

    def order(self, drink, heard):
        """음료 주문. 로봇이 쉬고 있을 때만 받는다. (응답 코드, 본문)을 돌려준다."""
        with self.lock:
            phase = self.state["phase"]
            if phase != "idle" or (self.session is not None and self.session.alive):  # 앞 세션이 장치를 놓기 전에는 안 받는다
                return 409, {"code": "hold" if phase == "hold" else "busy", "error": f"not accepting orders ({phase})"}
            steps = plan(drink_tasks(drink), self.options.duration_s)
            self._begin(drink, "list", heard, steps)
        return 202, {"drink": drink, "tasks": [task for task, _ in steps], "started": True}

    def manual(self, task, seconds):
        """팀이 넣은 task 하나. 돌고 있는 작업이 있으면 끼어들어 리스트를 버리고 이 문장만 돈다.

        초기 위치로 가는 중이거나 멈추는 중이면 끼어들 수 없어 None을 돌려준다. 화면 상태는 로봇이
        task_start(source=manual)를 알릴 때 바뀐다. 미리 바꾸면 끊긴 리스트 task가 수동 실행으로 기록된다.
        """
        with self.lock:
            session = self.session
            running = session is not None and session.alive
            if running and (session.mode != "infer" or self.state["phase"] == "stopping"):
                return None
            if not running:
                self._begin(None, "manual", None, [(task, seconds)])
        if running:
            session.preempt(task, seconds)
        return {"preempted": running}

    def home(self):
        """팔을 초기 자세로. 돌고 있으면 멈춘다(정상 종료 경로가 복귀를 한다). 쉬고 있으면 home 모드를 띄운다."""
        with self.lock:
            session = self.session
            if session is not None and session.alive:
                if self.state["phase"] == "stopping":
                    return "stopping"
                self.state["phase"] = "stopping"
                action = "stop"
            else:
                self.home_from = self.state["phase"]
                self._begin(None, "home", None, [(HOME_TASK, 0.0)], mode="home")
                action = "home"
        if action == "stop":
            self.note("home", via="stop")
            session.stop()
        return action

    def _begin(self, drink, source, heard, steps, mode="infer"):
        """lock을 쥔 채 부른다."""
        self.state.update(phase="loading", job=self.state["job"] + 1, drink=drink, source=source, heard=heard,
                          plan=[task for task, _ in steps], step=0, steps=len(steps), task=None, ready=False, last=None)
        self.started_at = time.time()
        self.note("start", drink=drink, source=source, tasks=len(steps))
        self.session = launch(self.options, steps, source, self._on_event, mode)

    def stop(self):
        """지금 작업을 정상 종료 경로로 멈춘다. 돌고 있는 작업이 없으면 False."""
        with self.lock:
            session = self.session
            if session is None or not session.alive:
                return False
            self.state["phase"] = "stopping"
        self.note("stop")
        session.stop()
        return True

    def resume(self):
        """hold를 풀고 다시 주문을 받는다. hold가 아니면 False."""
        with self.lock:
            if self.state["phase"] != "hold":
                return False
            self.state["phase"] = "idle"
        self.note("resume")
        return True

    def _on_event(self, event):
        """Session 스레드가 올리는 BEVERAGE 이벤트. docs/protocol.md의 stdout 규격."""
        kind = event.get("event")
        with self.lock:
            state = self.state
            if kind == "ready":
                state["ready"] = True
            elif kind == "task_start":
                if state["phase"] != "stopping":
                    state["phase"] = "running"
                source = event.get("source", state["source"])
                if source == "manual":
                    state.update(drink=None, heard=None, plan=[event.get("task")])
                state.update(step=event.get("index", 0), steps=event.get("total", state["steps"]),
                             task=event.get("task"), source=source)
            elif kind == "task_end":
                self.log({"job": state["job"], "drink": state["drink"], "source": state["source"], "task": state["task"],
                          "reason": event.get("reason"), "seconds": event.get("seconds"), "heard": state["heard"]})
            elif kind == "done":
                elapsed = round(time.time() - self.started_at, 1) if self.started_at else None
                state["last"] = {"reason": event.get("reason", "error"), "drink": state["drink"],
                                 "source": state["source"], "seconds": elapsed, "message": event.get("message", "")}
                # 작업대 정리 전까지 주문을 받지 않는다. 주문을 받던 중에 누른 초기 위치 이동만 성공하면 다시 받는다
                homed = state["source"] == "home" and state["last"]["reason"] == "complete" and self.home_from == "idle"
                state.update(phase="idle" if homed else "hold", task=None)
                self.log({"job": state["job"], "drink": state["drink"], "source": state["source"], "done": state["last"]})
        self.note("robot", **{k: v for k, v in event.items() if k != "message"})
        if kind == "done" and event.get("message"):
            print(event["message"], flush=True)

    def debug(self):
        options = self.options
        config = {name: getattr(options, name) for name in DEBUG_OPTIONS}
        config = {k: (str(v) if hasattr(v, "parts") else v) for k, v in config.items()}  # Path는 문자열로
        config["stt_api_key_set"] = bool(self.api_key)
        session = self.session
        return {"config": config, "events": list(self.events),
                "stderr_tail": list(session.stderr_tail) if session else [],
                "commands": [subprocess.list2cmdline(c) for c in session.commands] if session else []}


class Handler(BaseHTTPRequestHandler):
    """GET: / /team /status /debug. POST: /command /speak (주문), /infer /stop /resume /home (팀)."""

    def log_message(self, *args):  # 화면이 2초마다 /status를 물어서 기본 접속 로그를 두면 콘솔이 넘친다
        pass

    def reply(self, code, data, content_type="application/json; charset=utf-8"):
        payload = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def allowed(self, token, need):
        """토큰 권한이 need 이상이면 True. 아니면 403을 보내고 False."""
        role = self.server.role(token)
        if role is not None and ROLES.index(role) >= ROLES.index(need):
            return True
        self.reply(403, {"code": "token", "error": "bad token" if role is None else f"{need} token required"})
        return False

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path in PAGES:
            # 페이지는 폰에서 링크로 바로 여니 토큰을 주소에서 읽는다. 나머지 요청은 X-Token 헤더로 받는다.
            name, need = PAGES[path]
            if not self.allowed(dict(p.split("=", 1) for p in query.split("&") if "=" in p).get("token"), need):
                return
            page = (WEB / name).read_text(encoding="utf-8").replace("__TASKS__", json.dumps(page_tasks(), ensure_ascii=False))
            self.reply(200, page.encode(), "text/html; charset=utf-8")
        elif path == "/status":
            if self.allowed(self.headers.get("X-Token"), "demo"):
                with self.server.lock:
                    state = dict(self.server.state)
                state["accepting"] = state["phase"] == "idle"
                self.reply(200, state)
        elif path == "/debug":
            if self.allowed(self.headers.get("X-Token"), "team"):
                self.reply(200, self.server.debug())
        else:
            self.reply(404, {"error": "not found"})

    def answer(self, code, data, value):
        self.server.note("request", path=self.path, input=value, code=code,
                         result=data.get("error") or data.get("drink") or data.get("task") or "")
        self.reply(code, data)

    def do_POST(self):
        if not self.allowed(self.headers.get("X-Token"), "team" if self.path in TEAM_PATHS else "demo"):
            return
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path == "/stop":
            if self.server.stop():
                return self.answer(202, {"stopping": True}, "")
            return self.answer(409, {"code": "idle", "error": "nothing is running"}, "")
        if self.path == "/resume":
            if self.server.resume():
                return self.answer(202, {"accepting": True}, "")
            return self.answer(409, {"code": "not_hold", "error": "not on hold"}, "")
        if self.path == "/home":
            action = self.server.home()
            if action == "stopping":
                return self.answer(409, {"code": "stopping", "error": "already stopping"}, "")
            return self.answer(202, {"action": action, "started": True}, action)
        # 수동 실행은 키워드 판정 없이 task와 duration_s만 받는다. 이름은 infer 옵션과 같고 task에는 단계 이름도 된다.
        # 학습 안 된 문장이나 --no-target-limit 같은 안전 제한 해제가 원격으로 넘어오면 곤란해서 그 밖의 값은 받지 않는다.
        if self.path == "/infer":
            try:
                request = json.loads(body)
                task = resolve_task(request["task"])
                duration_s = request.get("duration_s")
                seconds = DEFAULT_DURATION_S[task] if duration_s is None else check_manual_duration(duration_s)
                if self.server.options.duration_s and duration_s is None:
                    seconds = self.server.options.duration_s
            except (ValueError, KeyError, TypeError, AttributeError) as error:
                return self.answer(400, {"code": "bad_request", "error": f"expected JSON {{task, duration_s?}}: {error}"},
                                   body[:200].decode("utf-8", "replace"))
            outcome = self.server.manual(task, seconds)
            if outcome is None:
                return self.answer(409, {"code": "busy", "error": "stopping or returning to the initial position; wait"}, task)
            print(f"Team manual: {task!r} {seconds:g}s preempted={outcome['preempted']}", flush=True)
            return self.answer(202, {"task": task, "duration_s": seconds, "started": True, **outcome}, task)
        if self.path == "/command":
            try:
                text = json.loads(body)["text"]
                if not isinstance(text, str):
                    raise TypeError("text must be a string")
            except (ValueError, KeyError, TypeError) as error:
                return self.answer(400, {"code": "bad_request", "error": f"expected JSON {{text}}: {error}"},
                                   body[:200].decode("utf-8", "replace"))
        elif self.path == "/speak":  # 본문이 녹음 파일 그대로다. 받아 적은 뒤에는 /command와 같은 길로 간다
            audio = f"{len(body)} bytes {self.headers.get('Content-Type', '')}"
            if not self.server.api_key:
                key_env = PROVIDERS[self.server.options.stt_provider]["key_env"]
                return self.answer(503, {"code": "no_key", "error": f"{key_env} is not set on the PC"}, audio)
            try:
                text = transcribe(body, self.headers.get("Content-Type", "audio/webm"),
                                  self.server.options.stt_model, self.server.api_key, STT_PROMPT,
                                  self.server.options.stt_provider)
            except TranscribeError as error:
                print(f"Transcription failed [{error.code}]: {error}", flush=True)
                return self.answer(502, {"code": error.code, "error": str(error)}, audio)
        else:
            return self.reply(404, {"error": "not found"})
        drinks = drinks_in(text)
        print(f"Phone: {text!r} -> {sorted(drinks)}", flush=True)
        if not drinks:
            return self.answer(400, {"code": "no_drink", "text": text, "error": "no drink keyword"}, text)
        if len(drinks) > 1:
            return self.answer(400, {"code": "ambiguous", "text": text, "error": "more than one drink"}, text)
        code, data = self.server.order(drinks.pop(), text)
        self.answer(code, {"text": text, **data}, text)


def tailscale_name():
    """이 PC의 테일스케일 이름(예: pc.tailnet.ts.net). 테일스케일이 없거나 꺼져 있으면 None.

    시작할 때 폰에서 열 링크를 찍어 주는 데만 쓴다. 실패해도 서버는 그대로 뜬다.
    """
    try:
        out = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True,
                             encoding="utf-8", timeout=10, check=True).stdout
        return json.loads(out)["Self"]["DNSName"].rstrip(".") or None
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return None


def tailscale_serving(port):
    """tailscale serve가 이 포트를 중계하고 있으면 True, 모르면 None."""
    try:
        out = subprocess.run(["tailscale", "serve", "status"], capture_output=True, text=True,
                             encoding="utf-8", timeout=10).stdout
        return f":{port}" in out
    except (OSError, subprocess.SubprocessError):
        return None


def check_infer(options):
    """서버를 띄우기 전에 infer를 --validate-only로 한 번 돌려 본다. 실패하면 SystemExit."""
    if options.robot_protocol == "task_list":
        steps = plan(drink_tasks("greentea"), options.duration_s)
    else:
        steps = plan([POWDER_TASKS["greentea"]], options.duration_s)
    print("Checking infer with --validate-only (no motors, no weights)...", flush=True)
    result, tail = validate(options, steps)
    if result != "complete":
        if "unrecognized arguments: --tasks" in tail:  # 패치 전 로봇 저장소
            tail += "\nThe robot repo does not know --tasks yet: apply robot_side/*.patch, or run with --robot-protocol single."
        raise SystemExit("infer --validate-only failed; fix this before serving. Last lines from infer:\n" + tail)
    print("infer check passed.", flush=True)


def resolve_tokens(options):
    """토큰은 옵션, 환경 변수, 무작위 순서. 폰에 북마크해 두려면 환경 변수로 고정한다."""
    tokens = {"demo": options.token or os.environ.get("BEVERAGE_DEMO_TOKEN") or secrets.token_urlsafe(9),
              "team": options.team_token or os.environ.get("BEVERAGE_TEAM_TOKEN") or secrets.token_urlsafe(9)}
    if tokens["demo"] == tokens["team"]:
        raise SystemExit("demo and team tokens must differ; the demo link would otherwise allow stop and manual runs.")
    return tokens


def run_phone(options):
    tokens = resolve_tokens(options)
    key_env = PROVIDERS[options.stt_provider]["key_env"]
    api_key = os.environ.get(key_env, "")
    if not options.dry_run:
        check_infer(options)
    server = Server((options.host, options.port), options, tokens, api_key)
    if options.host not in ("127.0.0.1", "localhost"):
        # 테일스케일 IP 같은 주소에 직접 연 경우. HTTP라 폰 브라우저가 마이크를 막고 버튼만 된다.
        base = f"http://{options.host}:{options.port}"
        print("Direct http bind: phone microphone stays blocked, buttons only.", flush=True)
    else:
        name = tailscale_name()
        base = f"https://{name}" if name else f"http://127.0.0.1:{options.port}"
        if name:
            serving = tailscale_serving(options.port)
            print("Tailscale serve: " + ("on" if serving else f"OFF, run once: tailscale serve --bg {options.port}"
                                          if serving is False else "unknown"), flush=True)
        else:
            print("Tailscale not found: links below work on this PC only.", flush=True)
    print(f"Demo page (keep private): {base}/?token={tokens['demo']}", flush=True)
    print(f"Team page (keep private): {base}/team?token={tokens['team']}", flush=True)
    local = f"http://127.0.0.1:{options.port}"
    if options.host in ("127.0.0.1", "localhost"):
        # 브라우저는 127.0.0.1을 안전한 주소로 보고 HTTP여도 마이크를 허락한다. Tailscale 없이 이 PC 마이크로 시험할 때
        print(f"On this PC, no Tailscale needed (microphone works): {local}/?token={tokens['demo']}", flush=True)
        print(f"On this PC, team page: {local}/team?token={tokens['team']}", flush=True)
    if api_key:
        print(f"Speech: {options.stt_provider} {options.stt_model}", flush=True)
    else:
        print(f"{key_env} is not set: hold-to-talk will answer 503; the team page buttons still work.", flush=True)
    print(f"Robot protocol: {options.robot_protocol}; run log: {LOG_PATH}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        if server.stop():
            print("Stopping the robot before exit...", flush=True)
            server.session.wait(STOP_GRACE_S + 10)
        print("Stopped.", flush=True)
