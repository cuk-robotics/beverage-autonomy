"""장치 없이 돌리는 검사. 로봇 자리에는 tests/fake_robot의 가짜 run_robot.py나 --dry-run이 들어간다."""
import contextlib
import http.client
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from beverage_voice import cli, inference, phone, stt, tasks  # noqa: E402

FAKE_ROBOT = Path(__file__).resolve().parent / "fake_robot"
BASE_ARGS = ["--project-root", str(FAKE_ROBOT), "--policy-path", "models/m", "--dataset-root", "data/d",
             "--python", sys.executable]
DEMO, TEAM = "demo-secret", "team-secret"
os.environ["FAKE_ROBOT_TASK_S"] = "0.25"


def parse(*args):
    """cli.main과 같은 순서로 옵션을 읽고 검사한다. 잘못된 조합이면 SystemExit."""
    parser = cli.build_parser()
    options, extra = parser.parse_known_args([*args, *BASE_ARGS])
    options.infer_extra = extra
    with contextlib.redirect_stderr(io.StringIO()):
        cli.validate_options(parser, options)
    return options


def wait_for(predicate, timeout=10.0):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class Recorder:
    """Session 이벤트를 모은다."""

    def __init__(self):
        self.events = []

    def __call__(self, event):
        self.events.append({**event, "at": time.perf_counter()})

    def kinds(self):
        return [e["event"] for e in self.events]

    def first(self, kind, **match):
        return next(e for e in self.events if e["event"] == kind and all(e.get(k) == v for k, v in match.items()))

    def seen(self, kind, **match):
        return any(e["event"] == kind and all(e.get(k) == v for k, v in match.items()) for e in self.events)


class TasksTests(unittest.TestCase):
    def test_keywords_map_to_dataset_drink_names(self):
        self.assertEqual(tasks.match_drink("말차 타"), "greentea")
        self.assertEqual(tasks.match_drink("아이스 티 부탁해"), "icetea")
        self.assertEqual(tasks.match_drink("커피 만들어줘"), "coffee")

    def test_no_or_ambiguous_keyword(self):
        self.assertIsNone(tasks.match_drink("물 주세요"))
        self.assertIsNone(tasks.match_drink("이 카페 로봇 신기하다"))  # "카페"는 키워드가 아니다
        self.assertEqual(tasks.drinks_in("커피 말고 말차"), {"coffee", "greentea"})
        self.assertIsNone(tasks.match_drink("커피 말고 말차"))

    def test_misheard_and_english_names(self):
        for text, drink in (("말자 타 줘", "greentea"), ("마차 주세요", "greentea"), ("녹자 라떼", "greentea"),
                            ("Matcha latte", "greentea"), ("아이스띠", "icetea"), ("아이쓰티 주세요", "icetea"),
                            ("Iced tea please", "icetea"), ("아이스T", "icetea"), ("카피 한 잔", "coffee"),
                            ("꺼피", "coffee"), ("아메리카노 주세요", "coffee"), ("아이스 커피", "coffee")):
            self.assertEqual(tasks.match_drink(text), drink, text)
        for text in ("오늘 알차게 놀자", "발차", "녹초가 됐다", "아이스크림", "커플", "차 한잔", "안녕하세요"):
            self.assertIsNone(tasks.match_drink(text), text)

    def test_drink_lists(self):
        self.assertEqual(tasks.drink_tasks("greentea"), [tasks.POWDER_TASKS["greentea"], tasks.WATER_TASK, tasks.STIR_TASK])
        self.assertEqual(tasks.drink_tasks("icetea"), [tasks.POWDER_TASKS["icetea"], tasks.WATER_TASK])  # 젓기 데이터 없음

    def test_caps_and_plan(self):
        self.assertEqual([tasks.DEFAULT_DURATION_S[t] for t in tasks.drink_tasks("coffee")], [100.0, 75.0, 90.0])
        self.assertEqual(tasks.plan([tasks.WATER_TASK]), [(tasks.WATER_TASK, 75.0)])
        self.assertEqual(tasks.plan([tasks.WATER_TASK, tasks.STIR_TASK], 30), [(tasks.WATER_TASK, 30.0), (tasks.STIR_TASK, 30.0)])

    def test_task_names_resolve_and_bad_values_fail(self):
        self.assertEqual(tasks.resolve_task("water"), tasks.WATER_TASK)
        self.assertEqual(tasks.resolve_task(tasks.STIR_TASK), tasks.STIR_TASK)
        for value in ("dance", "Pour juice into the cup."):
            with self.assertRaises(ValueError):
                tasks.resolve_task(value)
        for value in (0, 301, float("nan"), "abc"):
            with self.assertRaises(ValueError):
                tasks.check_manual_duration(value)


class InferArgsTests(unittest.TestCase):
    def test_task_list_protocol_sends_the_whole_plan(self):
        options = parse("phone", "--end-detector", "C:/robot/eed/config.json", "--no-target-limit", "--rerun-port", "9999")
        steps = tasks.plan(tasks.drink_tasks("coffee"))
        args = inference.infer_args(options, steps)
        self.assertEqual(args[0], "infer")
        self.assertEqual(json.loads(args[args.index("--tasks") + 1]), tasks.drink_tasks("coffee"))
        self.assertEqual(json.loads(args[args.index("--task-durations") + 1]), [100.0, 75.0, 90.0])
        self.assertEqual(args[args.index("--end-detector") + 1], str(Path("C:/robot/eed/config.json")))
        self.assertIn("--no-target-limit", args)
        self.assertIn("--return-to-initial-position", args)
        self.assertNotIn("--task", args)
        self.assertEqual(args[-2:], ["--rerun-port", "9999"])  # 모르는 옵션은 맨 뒤에 그대로

    def test_single_protocol_sends_one_task(self):
        options = parse("phone", "--robot-protocol", "single", "--no-return-to-initial-position", "--fps", "15")
        args = inference.infer_args(options, [(tasks.WATER_TASK, 75.0)])
        self.assertEqual(args[args.index("--task") + 1], tasks.WATER_TASK)
        self.assertEqual(args[args.index("--duration-s") + 1], "75.0")
        self.assertEqual(args[args.index("--fps") + 1], "15")
        self.assertNotIn("--tasks", args)
        self.assertNotIn("--return-to-initial-position", args)


class SessionTests(unittest.TestCase):
    """가짜 run_robot.py를 실제 자식 프로세스로 띄운다."""

    def run_session(self, options, steps, source="list", until=None, action=None):
        recorder = Recorder()
        session = inference.Session(options, steps, source, recorder).start()
        if until:
            self.assertTrue(wait_for(lambda: recorder.seen(**until)), f"never saw {until}: {recorder.kinds()}")
            action(session)
        session.wait(15)
        self.assertFalse(session.alive, "session did not finish")
        return session, recorder

    def test_task_list_runs_the_whole_plan_in_one_process(self):
        session, rec = self.run_session(parse("phone", "--end-detector", "eed.json"), tasks.plan(tasks.drink_tasks("greentea")))
        self.assertEqual(session.result, "complete")
        self.assertEqual(len(session.commands), 1)
        self.assertEqual(rec.kinds(), ["ready", "task_start", "task_end", "task_start", "task_end", "task_start", "task_end", "done"])
        self.assertEqual([e["reason"] for e in rec.events if e["event"] == "task_end"], ["detector"] * 3)
        self.assertEqual(rec.first("task_start", index=2)["task"], tasks.WATER_TASK)

    def test_limit_without_detector_moves_on_but_with_detector_stops(self):
        session, rec = self.run_session(parse("phone"), tasks.plan(tasks.drink_tasks("icetea")))
        self.assertEqual(([e["reason"] for e in rec.events if e["event"] == "task_end"], session.result),
                         (["duration", "duration"], "complete"))
        session, rec = self.run_session(parse("phone", "--end-detector", "eed.json", "--duration-s", "0.1"),
                                        tasks.plan(tasks.drink_tasks("coffee"), 0.1))
        self.assertEqual(([e["reason"] for e in rec.events if e["event"] == "task_end"], session.result),
                         (["duration"], "timeout"))

    def test_done_is_reported_only_after_the_robot_process_exits(self):
        os.environ["FAKE_ROBOT_TEARDOWN_S"] = "1.0"  # done 뒤 복귀·연결 해제 시간
        try:
            session, rec = self.run_session(parse("phone"), tasks.plan([tasks.WATER_TASK]))
        finally:
            del os.environ["FAKE_ROBOT_TEARDOWN_S"]
        self.assertEqual(session.result, "complete")
        gap = rec.first("done")["at"] - rec.first("task_end")["at"]
        self.assertGreater(gap, 0.8)  # 로봇이 done을 찍은 직후가 아니라 프로세스가 끝난 뒤에 올라왔다

    def test_task_list_stop_ends_cleanly(self):
        session, rec = self.run_session(parse("phone"), tasks.plan(tasks.drink_tasks("coffee")),
                                        until={"kind": "task_start"}, action=lambda s: s.stop())
        self.assertEqual(session.result, "stop")
        self.assertEqual(rec.events[-2]["reason"], "stop")
        self.assertFalse(rec.seen("task_start", index=2))

    def test_task_list_manual_command_preempts_the_list(self):
        session, rec = self.run_session(parse("phone"), tasks.plan(tasks.drink_tasks("coffee")),
                                        until={"kind": "task_start"}, action=lambda s: s.preempt(tasks.WATER_TASK, 75))
        self.assertEqual(session.result, "complete")
        self.assertTrue(rec.seen("task_end", reason="preempted"))
        manual = rec.first("task_start", source="manual")
        self.assertEqual((manual["task"], manual["total"]), (tasks.WATER_TASK, 1))
        self.assertFalse(rec.seen("task_start", task=tasks.STIR_TASK))  # 리스트는 버려졌다

    def test_single_protocol_runs_each_task_in_its_own_process(self):
        options = parse("phone", "--robot-protocol", "single")
        session, rec = self.run_session(options, tasks.plan(tasks.drink_tasks("greentea")))
        self.assertEqual(session.result, "complete")
        self.assertEqual(len(session.commands), 3)
        self.assertEqual([e["reason"] for e in rec.events if e["event"] == "task_end"], ["duration"] * 3)

    def test_single_protocol_stop_interrupts_the_child(self):
        options = parse("phone", "--robot-protocol", "single")
        output = io.StringIO()

        def stop_once_running(session):  # 자식이 신호 핸들러를 건 뒤에 보낸다
            self.assertTrue(wait_for(lambda: "single task running" in output.getvalue()))
            session.stop()
        with contextlib.redirect_stdout(output):
            session, rec = self.run_session(options, tasks.plan(tasks.drink_tasks("greentea")),
                                            until={"kind": "task_start"}, action=stop_once_running)
        self.assertEqual(session.result, "stop")
        self.assertEqual(len(session.commands), 1)
        self.assertIn("interrupted", output.getvalue())  # 자식이 Ctrl+C 경로로 끝났다

    def test_stop_before_the_child_exists_is_not_lost(self):
        options = parse("phone", "--robot-protocol", "single")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            # task_start는 프로세스를 띄우기 전에 나온다. 그 틈에 들어온 stop도 첫 task에 전달돼야 한다
            session, rec = self.run_session(options, tasks.plan(tasks.drink_tasks("greentea")),
                                            until={"kind": "task_start"}, action=lambda s: s.stop())
        self.assertEqual(session.result, "stop")
        self.assertNotIn("task finished", output.getvalue())

    def test_single_protocol_preempt_restarts_with_the_manual_task(self):
        options = parse("phone", "--robot-protocol", "single")
        with contextlib.redirect_stdout(io.StringIO()):
            session, rec = self.run_session(options, tasks.plan(tasks.drink_tasks("greentea")),
                                            until={"kind": "task_start"}, action=lambda s: s.preempt(tasks.WATER_TASK, 75))
        self.assertEqual(session.result, "complete")
        self.assertEqual(len(session.commands), 2)
        self.assertEqual(session.commands[1][session.commands[1].index("--task") + 1], tasks.WATER_TASK)
        self.assertTrue(rec.seen("task_end", reason="preempted"))
        self.assertTrue(rec.seen("task_start", source="manual", task=tasks.WATER_TASK))

    def test_home_mode_runs_the_home_command(self):
        recorder = Recorder()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            session = inference.Session(parse("phone"), [], "home", recorder, mode="home").start()
            session.wait(15)
        self.assertEqual(session.result, "complete")
        self.assertEqual(session.commands[0][2:], ["home"])
        self.assertEqual(recorder.kinds(), ["ready", "task_start", "task_end", "done"])
        self.assertEqual(recorder.first("task_start")["task"], inference.HOME_TASK)
        self.assertIn("homed", output.getvalue())
        self.assertFalse(session.preempt(tasks.WATER_TASK, 75))  # home 세션은 끼어들기를 받지 않는다

    def test_child_failure_is_reported_with_its_stderr(self):
        options = parse("phone", "--boom")
        session, rec = self.run_session(options, tasks.plan([tasks.WATER_TASK]))
        self.assertEqual(session.result, "error")
        self.assertIn("boom", rec.events[-1]["message"])
        self.assertIn("code 3", rec.events[-1]["message"])

    def test_validate_only_passes_through(self):
        result, tail = inference.validate(parse("phone"), tasks.plan([tasks.WATER_TASK]))
        self.assertEqual(result, "complete")
        self.assertEqual(inference.validate(parse("phone", "--boom"), tasks.plan([tasks.WATER_TASK]))[0], "error")


class DryRunTests(unittest.TestCase):
    def setUp(self):
        self.saved = inference.DryRunSession.task_seconds
        inference.DryRunSession.task_seconds = 0.1

    def tearDown(self):
        inference.DryRunSession.task_seconds = self.saved

    def test_dry_run_prints_the_command_and_fakes_the_events(self):
        recorder = Recorder()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            session = inference.launch(parse("phone", "--dry-run"), tasks.plan(tasks.drink_tasks("icetea")), "list", recorder)
            session.wait(5)
        self.assertIn("--tasks", output.getvalue())
        self.assertEqual(session.result, "complete")
        self.assertEqual(rec_kinds := recorder.kinds(), ["ready", "task_start", "task_end", "task_start", "task_end", "done"], rec_kinds)

    def test_dry_run_stop_and_preempt(self):
        recorder = Recorder()
        with contextlib.redirect_stdout(io.StringIO()):
            session = inference.launch(parse("phone", "--dry-run"), tasks.plan(tasks.drink_tasks("coffee")), "list", recorder)
            self.assertTrue(wait_for(lambda: recorder.seen("task_start")))
            session.preempt(tasks.STIR_TASK, 90)
            self.assertTrue(wait_for(lambda: recorder.seen("task_start", source="manual")))
            session.stop()
            session.wait(5)
        self.assertEqual(session.result, "stop")


class CliTests(unittest.TestCase):
    def test_mode_defaults(self):
        saved = os.environ.pop("GROQ_API_KEY", None)
        try:
            options = parse("phone")
            self.assertEqual((options.stt_provider, options.stt_model, options.host, options.port, options.robot_protocol),
                             ("openai", "whisper-1", "127.0.0.1", 8000, "task_list"))
            os.environ["GROQ_API_KEY"] = "test-key"  # Groq 키가 있으면 Groq Whisper가 기본
            self.assertEqual((parse("phone").stt_provider, parse("phone").stt_model), ("groq", "whisper-large-v3"))
            self.assertEqual(parse("phone", "--stt-provider", "openai").stt_model, "whisper-1")
        finally:
            os.environ.pop("GROQ_API_KEY", None)
            if saved is not None:
                os.environ["GROQ_API_KEY"] = saved

    def test_bad_combinations_are_refused(self):
        for args in (("phone", "--task", "water"), ("run", "--task", "water", "--host", "0.0.0.0"), ("run",),
                     ("run", "--task", "dance"), ("run", "--task", "water", "--duration-s", "999"),
                     ("phone", "--fps", "500"), ("phone", "--robot-protocol", "single", "--end-detector", "x.json")):
            with self.assertRaises(SystemExit, msg=args):
                parse(*args)

    def test_relative_project_root_is_made_absolute(self):
        relative = os.path.relpath(FAKE_ROBOT)
        parser = cli.build_parser()
        options, extra = parser.parse_known_args(["phone", "--project-root", relative, "--policy-path", "m", "--dataset-root", "d"])
        options.infer_extra = extra
        cli.validate_options(parser, options)
        self.assertTrue(options.project_root.is_absolute())
        command = inference.infer_command(options, tasks.plan([tasks.WATER_TASK]))
        self.assertEqual(Path(command[1]), FAKE_ROBOT / "scripts" / "run_robot.py")

    def test_env_file_fills_but_never_overrides(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("# 주석\nBV_TEST_A=from-file\nBV_TEST_B = 'quoted'\nBV_TEST_C=from-file\n", encoding="utf-8")
            os.environ["BV_TEST_C"] = "from-shell"
            try:
                cli.load_env_file(path)
                self.assertEqual([os.environ[k] for k in ("BV_TEST_A", "BV_TEST_B", "BV_TEST_C")],
                                 ["from-file", "quoted", "from-shell"])
            finally:
                for key in ("BV_TEST_A", "BV_TEST_B", "BV_TEST_C"):
                    os.environ.pop(key, None)

    def test_dry_run_needs_no_robot_paths(self):
        parser = cli.build_parser()
        options, extra = parser.parse_known_args(["phone", "--dry-run"])
        options.infer_extra = extra
        cli.validate_options(parser, options)  # 로봇 저장소 없이 이 PC에서 음성 흐름만 본다
        self.assertIn("--tasks", inference.infer_command(options, tasks.plan([tasks.WATER_TASK])))

    def test_project_root_without_launcher_is_refused(self):
        parser = cli.build_parser()
        options, extra = parser.parse_known_args(["run", "--task", "water", "--project-root", str(ROOT),
                                                  "--policy-path", "m", "--dataset-root", "d"])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.validate_options(parser, options)

    def test_run_mode_dry_run_prints_command_and_events(self):
        saved, inference.DryRunSession.task_seconds = inference.DryRunSession.task_seconds, 0.05
        try:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                cli.main(["run", "--task", "stir", "--duration-s", "70", "--dry-run", *BASE_ARGS])
        finally:
            inference.DryRunSession.task_seconds = saved
        text = output.getvalue()
        self.assertIn(f"[manual] (70s): {tasks.STIR_TASK}", text)
        self.assertIn('--task-durations [70.0]', text)
        self.assertIn('"event": "done"', text)


class PhoneTests(unittest.TestCase):
    """토큰 권한, 주문·중지·끼어들기·hold 흐름. 로봇 대신 --dry-run으로 돈다."""

    @classmethod
    def setUpClass(cls):
        cls.saved_speed, inference.DryRunSession.task_seconds = inference.DryRunSession.task_seconds, 0.15
        cls.log = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        cls.log.close()
        cls.saved_log, phone.LOG_PATH = phone.LOG_PATH, Path(cls.log.name)
        with contextlib.redirect_stdout(io.StringIO()):
            cls.server = phone.Server(("127.0.0.1", 0), parse("phone", "--dry-run"), {"demo": DEMO, "team": TEAM}, api_key="")
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        inference.DryRunSession.task_seconds = cls.saved_speed
        phone.LOG_PATH = cls.saved_log
        os.unlink(cls.log.name)

    def request(self, method, path, body=None, token=TEAM, content_type="application/json"):
        body = body.encode("utf-8") if isinstance(body, str) else body
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers={"X-Token": token, "Content-Type": content_type})
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode()) if response.getheader("Content-Type", "").startswith("application/json") else response.read().decode()
        finally:
            connection.close()

    def status(self):
        return self.request("GET", "/status", token=DEMO)[1]

    def settle(self):
        """지금 작업이 끝나 hold가 될 때까지 기다린 뒤 idle로 돌린다."""
        self.assertTrue(wait_for(lambda: self.status()["phase"] == "hold", 10), self.status())
        self.assertEqual(self.request("POST", "/resume", "{}")[0], 202)
        self.assertEqual(self.status()["phase"], "idle")

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            if self.status()["phase"] in ("loading", "running"):
                self.request("POST", "/stop", "{}")
            if self.status()["phase"] != "idle":
                self.settle()

    def test_pages_need_the_matching_token(self):
        for path in ("/", "/?token=wrong", "/team", f"/team?token={DEMO}"):
            self.assertEqual(self.request("GET", path)[0], 403, path)
        status, demo = self.request("GET", f"/?token={DEMO}")
        self.assertEqual(status, 200)
        self.assertIn("누르고 말하기", demo)
        for path in ("/infer", "/stop", "/home", "/command"):  # 사용자 화면은 음성 주문뿐이다
            self.assertNotIn(f'"{path}"', demo, path)
        status, team = self.request("GET", f"/team?token={TEAM}")
        self.assertEqual(status, 200)
        self.assertNotIn("__TASKS__", team)
        self.assertIn('"powder_greentea"', team)
        self.assertEqual(self.request("GET", f"/?token={TEAM}")[0], 200)  # 팀 토큰은 시연 화면도 연다

    def test_hold_starts_only_after_the_robot_lets_go(self):
        """실제 자식 프로세스. done 뒤 복귀하는 동안 초기 위치 버튼을 눌러도 멈춤 상태에 갇히지 않는다."""
        os.environ["FAKE_ROBOT_TEARDOWN_S"] = "1.0"
        server = phone.Server(("127.0.0.1", 0), parse("phone"), {"demo": "d-1", "team": "t-1"}, api_key="")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(server.order("icetea", "아이스티")[0], 202)
                self.assertTrue(wait_for(lambda: server.state["phase"] == "hold", 15))
                self.assertFalse(server.session.alive)  # hold가 됐을 때는 장치를 이미 놓았다
                self.assertEqual(server.home(), "home")
                self.assertTrue(wait_for(lambda: not server.session.alive, 10))
                self.assertEqual(server.state["last"]["reason"], "complete")
        finally:
            del os.environ["FAKE_ROBOT_TEARDOWN_S"]
            server.server_close()
        self.assertEqual(server.role("한글토큰"), None)  # 한글이 와도 예외 없이 거절한다

    def test_home_stops_a_running_job_or_runs_the_home_mode(self):
        with contextlib.redirect_stdout(io.StringIO()):
            status, body = self.request("POST", "/home", "{}")  # 쉬고 있으면 home 모드
            self.assertEqual((status, body["action"]), (202, "home"))
            self.assertEqual(self.request("POST", "/infer", '{"task": "water"}')[1]["code"], "busy")  # 복귀 중엔 끼어들기 없음
            self.assertTrue(wait_for(lambda: self.status()["last"] is not None, 10))
            state = self.status()
            self.assertEqual((state["last"]["source"], state["plan"], state["last"]["reason"]), ("home", ["home"], "complete"))
            self.assertEqual(state["phase"], "idle")  # 주문을 받던 중이었으니 그대로 다시 받는다
            self.assertEqual(self.request("POST", "/command", '{"text":"커피"}', token=DEMO)[0], 202)
            self.assertTrue(wait_for(lambda: self.status()["phase"] == "running"))
            status, body = self.request("POST", "/home", "{}")  # 돌고 있으면 중지가 곧 복귀
            self.assertEqual((status, body["action"]), (202, "stop"))
            self.assertTrue(wait_for(lambda: self.status()["phase"] == "hold", 10))
        self.assertEqual(self.status()["last"]["reason"], "stop")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.request("POST", "/home", "{}")[1]["action"], "home")  # 잔 정리 중에 누르면
            self.assertTrue(wait_for(lambda: (self.status()["last"] or {}).get("source") == "home", 10))
        self.settle()  # 끝나도 hold 그대로라 주문 받기를 눌러야 한다

    def test_demo_token_cannot_stop_or_run_manual(self):
        for path in ("/infer", "/stop", "/resume", "/home"):
            self.assertEqual(self.request("POST", path, '{"task": "water"}', token=DEMO)[0], 403, path)
        self.assertEqual(self.request("GET", "/debug", token=DEMO)[0], 403)
        self.assertEqual(self.request("POST", "/command", '{"text":"말차"}', token="wrong")[0], 403)

    def test_order_runs_the_whole_list_then_holds(self):
        with contextlib.redirect_stdout(io.StringIO()):
            status, body = self.request("POST", "/command", '{"text":"말차 타"}', token=DEMO)
            self.assertEqual((status, body["drink"], body["tasks"]), (202, "greentea", tasks.drink_tasks("greentea")))
            self.assertIn(self.status()["phase"], ("loading", "running"))
            self.assertEqual(self.request("POST", "/command", '{"text":"커피"}', token=DEMO)[1]["code"], "busy")
            self.assertTrue(wait_for(lambda: self.status()["phase"] == "hold", 10))
        state = self.status()
        self.assertEqual((state["accepting"], state["last"]["reason"], state["last"]["drink"], state["heard"]),
                         (False, "complete", "greentea", "말차 타"))
        self.assertEqual(self.request("POST", "/command", '{"text":"커피"}', token=DEMO)[1]["code"], "hold")
        self.assertEqual(self.request("POST", "/resume", "{}")[0], 202)
        self.assertTrue(self.status()["accepting"])
        lines = [json.loads(line) for line in Path(self.log.name).read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(line.get("done", {}).get("reason") == "complete" for line in lines))

    def test_manual_command_preempts_a_running_list(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.request("POST", "/command", '{"text":"커피"}', token=DEMO)[0], 202)
            self.assertTrue(wait_for(lambda: self.status()["phase"] == "running"))
            status, body = self.request("POST", "/infer", '{"task": "water", "duration_s": 45}')
            self.assertEqual((status, body["task"], body["duration_s"], body["preempted"]), (202, tasks.WATER_TASK, 45, True))
            self.assertTrue(wait_for(lambda: self.status()["phase"] == "hold", 10))
        state = self.status()
        self.assertEqual((state["last"]["source"], state["last"]["drink"], state["plan"]), ("manual", None, [tasks.WATER_TASK]))
        self.settle()

    def test_manual_command_while_idle_runs_alone(self):
        with contextlib.redirect_stdout(io.StringIO()):
            status, body = self.request("POST", "/infer", '{"task": "stir"}')
            self.assertEqual((status, body["preempted"], body["duration_s"]), (202, False, 90))
            self.assertTrue(wait_for(lambda: self.status()["phase"] == "hold", 10))
        self.assertEqual(self.status()["last"]["source"], "manual")
        self.settle()

    def test_stop_ends_the_job(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.request("POST", "/stop", "{}")[1]["code"], "idle")
            self.assertEqual(self.request("POST", "/command", '{"text":"아이스티"}', token=DEMO)[0], 202)
            self.assertTrue(wait_for(lambda: self.status()["phase"] == "running"))
            self.assertEqual(self.request("POST", "/stop", "{}")[0], 202)
            self.assertTrue(wait_for(lambda: self.status()["phase"] == "hold", 10))
        self.assertEqual(self.status()["last"]["reason"], "stop")
        self.settle()

    def test_bad_text_is_refused_with_a_code(self):
        self.assertEqual(self.request("POST", "/command", '{"text":"물 주세요"}')[1]["code"], "no_drink")
        self.assertEqual(self.request("POST", "/command", '{"text":"커피 말고 말차"}')[1]["code"], "ambiguous")
        self.assertEqual(self.request("POST", "/command", "not json")[0], 400)
        for body in ('{"task": "Pour juice into the cup."}', '{"task": "water", "duration_s": 0}', '{"duration_s": 10}'):
            self.assertEqual(self.request("POST", "/infer", body)[0], 400, body)
        self.assertEqual(self.status()["phase"], "idle")

    def test_speak_without_api_key_is_a_clear_503(self):
        status, body = self.request("POST", "/speak", b"audio", token=DEMO, content_type="audio/webm")
        self.assertEqual((status, body["code"]), (503, "no_key"))

    def test_debug_shows_config_without_project_root(self):
        config = self.request("GET", "/debug")[1]["config"]
        self.assertEqual((config["dry_run"], config["robot_protocol"], config["stt_api_key_set"]), (True, "task_list", False))
        self.assertNotIn("project_root", config)

    def test_tokens_come_from_options_or_environment(self):
        options = parse("phone", "--token", "a", "--team-token", "b")
        self.assertEqual(phone.resolve_tokens(options), {"demo": "a", "team": "b"})
        os.environ["BEVERAGE_DEMO_TOKEN"], os.environ["BEVERAGE_TEAM_TOKEN"] = "env-demo", "env-team"
        try:
            self.assertEqual(phone.resolve_tokens(parse("phone")), {"demo": "env-demo", "team": "env-team"})
        finally:
            del os.environ["BEVERAGE_DEMO_TOKEN"], os.environ["BEVERAGE_TEAM_TOKEN"]
        with self.assertRaises(SystemExit):
            phone.resolve_tokens(parse("phone", "--token", "same", "--team-token", "same"))

    def test_check_infer_fails_loudly_when_the_robot_rejects_the_options(self):
        with contextlib.redirect_stdout(io.StringIO()):
            phone.check_infer(parse("phone"))
            with self.assertRaises(SystemExit) as stopped:
                phone.check_infer(parse("phone", "--boom"))
        self.assertIn("boom", str(stopped.exception))

    def test_transcribe_error_codes(self):
        self.assertEqual(stt.TranscribeError("api_key", "x").code, "api_key")
        self.assertIn("audio/mp4", stt.EXTENSIONS)

    def test_transcribe_retries_a_stalled_connection_but_not_a_rejected_key(self):
        import urllib.error
        calls = []

        class Reply(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def flaky(request, timeout):
            calls.append(timeout)
            if len(calls) == 1:
                raise urllib.error.URLError("connect timed out")
            return Reply('{"text": "커피"}'.encode())

        def rejected(request, timeout):
            calls.append(timeout)
            raise urllib.error.HTTPError(request.full_url, 401, "no", {}, io.BytesIO(b"bad key"))

        original = stt.urllib.request.urlopen
        try:
            stt.urllib.request.urlopen = flaky
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(stt.transcribe(b"a", "audio/wav", "m", "k", provider="groq"), "커피")
            self.assertEqual(len(calls), 2)  # 첫 연결 실패 뒤 한 번 더
            calls.clear()
            stt.urllib.request.urlopen = rejected
            with self.assertRaises(stt.TranscribeError) as caught:
                stt.transcribe(b"a", "audio/wav", "m", "k", provider="groq")
            self.assertEqual((caught.exception.code, len(calls)), ("api_key", 1))
        finally:
            stt.urllib.request.urlopen = original

    def test_transcribe_sends_the_drink_hint(self):
        sent = {}

        class Reply(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout):
            sent["body"], sent["url"] = request.data, request.full_url
            return Reply('{"text": " 말차 타 "}'.encode())

        original, stt.urllib.request.urlopen = stt.urllib.request.urlopen, fake_urlopen
        try:
            text = stt.transcribe(b"audio", "audio/webm", "m", "k", tasks.STT_PROMPT, "groq")
        finally:
            stt.urllib.request.urlopen = original
        self.assertEqual(text, "말차 타")
        self.assertIn('name="prompt"'.encode(), sent["body"])
        self.assertIn("아이스티".encode(), sent["body"])
        self.assertEqual(sent["url"], "https://api.groq.com/openai/v1/audio/transcriptions")


if __name__ == "__main__":
    unittest.main()
