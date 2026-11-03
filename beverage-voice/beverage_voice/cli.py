"""폰 명령으로 run_robot.py infer를 부르는 실행기.

    python -m beverage_voice phone|run [옵션]

phone은 폰 페이지 서버, run은 PC 명령줄에서 task 하나를 바로 실행하는 모드다.

짜임은 로봇 저장소 teleoperation/cli.py를 따랐다. 파서 하나에 mode를 받고 모드에서 안 쓰는 옵션은
parser.error로 막는다. infer 옵션은 이름, 형식, 뜻이 그쪽과 같아서 합칠 때는 전용 옵션과 mode 분기만
그쪽 main()으로 옮기면 된다.
"""
import argparse
import json
import math
import os
import sys
from pathlib import Path

from .inference import launch
from .stt import PROVIDERS, default_provider
from .tasks import check_manual_duration, plan, resolve_task

# 커밋하지 않는 로컬 설정 파일(.gitignore에 있다). API 키와 화면 토큰을 KEY=VALUE 줄로 넣어 둔다.
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def load_env_file(path=ENV_FILE):
    """KEY=VALUE 줄을 환경 변수로 올린다. 셸에서 이미 정한 값은 덮지 않는다."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        name, sep, value = line.strip().partition("=")
        if sep and name and value.strip() and not name.startswith("#"):  # 빈 값은 견본 그대로라 건너뛴다
            os.environ.setdefault(name.strip(), value.strip().strip("'\""))


# 모드에서만 쓰는 옵션. 기본값을 None으로 받아 두고 주어졌는지로 잘못 붙인 옵션을 가려낸다.
MODE_ONLY = {
    "phone": ("host", "port", "token", "team_token", "stt_model", "stt_provider"),
    "run": ("task",),
}
# 모드별 기본값. 로봇 cli가 --fps 기본값을 모드마다 다르게 채우는 것과 같은 방식.
MODE_DEFAULTS = {
    "phone": {"host": "127.0.0.1", "port": 8000},
    "run": {},
}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                     allow_abbrev=False,
                                     epilog="여기 없는 옵션은 run_robot.py infer로 그대로 넘어간다 (--rerun-port 등).")
    parser.add_argument("--project-root", type=Path, default=os.environ.get("CUK_ROBOTICS_ROOT"),
                        help="smolvla-bimanual-manipulation 저장소 루트. 환경 변수 CUK_ROBOTICS_ROOT로도 된다")
    parser.add_argument("mode", choices=("phone", "run"))

    # 여기부터는 로봇 cli의 infer 옵션과 같다. 다른 점은 아래 두 기본값뿐이다.
    parser.add_argument("--validate-only", action="store_true",
                        help="infer가 설정과 feature 계약만 검사하고 모터는 연결하지 않는다")
    parser.add_argument("--wrist-fourcc", choices=("MJPG", "YUY2"), default="MJPG",
                        help="손목 카메라 USB 형식. 수집과 docs/inference.md 예시에 맞춰 기본 MJPG")
    parser.add_argument("--fps", type=int, help="제어 주기. 안 주면 infer가 데이터셋 fps를 쓴다")
    parser.add_argument("--duration-s", type=float,
                        help="task 상한(초)을 전부 이 값으로. 안 주면 task별 기본값 (가루 100, 물 75, 젓기 90)")
    parser.add_argument("--policy-path", type=Path, help="SmolVLA 체크포인트 폴더. 로봇 저장소 기준 상대 경로도 된다")
    parser.add_argument("--dataset-root", type=Path, help="feature 검사에 쓸 호환 데이터셋 폴더. 파인튜닝 데이터셋이 맞다")
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda", help="추론 장치")
    parser.add_argument("--no-target-limit", action="store_true", help="양팔 max_relative_target을 이번 실행에서만 끈다")
    parser.add_argument("--return-to-initial-position", action=argparse.BooleanOptionalAction, default=True,
                        help="끝나거나 멈출 때 시작 자세로 돌아간다. 기본 켜짐")
    parser.add_argument("--task", help="run: 실행할 task 문장 또는 단계 이름 (powder_coffee, water, stir 등)")

    # 로봇 쪽 프로토콜. docs/protocol.md 참고
    parser.add_argument("--robot-protocol", choices=("task_list", "single"), default="task_list",
                        help="task_list: 리스트 전체를 한 프로세스로 (기본). single: 옛 infer처럼 task마다 새 프로세스")
    parser.add_argument("--end-detector", type=Path,
                        help="task_list: 로봇 PC에 있는 episode_end_detector config.json 경로. 없으면 상한 시간만 쓴다")

    # 음성 실행기 전용
    parser.add_argument("--python", default=os.environ.get("CUK_ROBOTICS_PYTHON", sys.executable),
                        help="run_robot.py를 돌릴 파이썬. 기본은 CUK_ROBOTICS_PYTHON, 없으면 지금 쓰는 파이썬")
    parser.add_argument("--dry-run", action="store_true", help="infer 명령만 출력하고 로봇 없이 흐름을 흉내 낸다")
    parser.add_argument("--stt-provider", choices=tuple(PROVIDERS),
                        help="phone: 전사 서비스. 기본은 GROQ_API_KEY가 있으면 groq, 없으면 openai")
    parser.add_argument("--stt-model", help="phone: 전사 모델. 기본 groq whisper-large-v3, openai whisper-1")
    parser.add_argument("--host", help="phone: 서버를 열 주소 (기본 127.0.0.1, tailscale serve로 폰에 연다)")
    parser.add_argument("--port", type=int, help="phone: 서버 포트 (기본 8000, tailscale serve와 맞춘다)")
    parser.add_argument("--token", help="phone: 시연 화면 토큰 (음료 주문만). 환경 변수 BEVERAGE_DEMO_TOKEN으로도 된다")
    parser.add_argument("--team-token", help="phone: 팀 화면 토큰 (수동 실행·중지까지). 환경 변수 BEVERAGE_TEAM_TOKEN으로도 된다")
    return parser


def validate_options(parser, options):
    """로봇 cli처럼 잘못된 조합은 장치를 열기 전에 parser.error로 멈춘다."""
    if options.dry_run:  # 명령을 출력만 하니 로봇 저장소와 정책이 없어도 된다. 음성 흐름만 이 PC에서 본다
        options.project_root = options.project_root or Path("smolvla-bimanual-manipulation")
        options.policy_path = options.policy_path or Path("models/YOUR_PRETRAINED_MODEL")
        options.dataset_root = options.dataset_root or Path("data/lerobot/YOUR_DATASET")
    elif options.project_root is None or not (Path(options.project_root) / "scripts" / "run_robot.py").is_file():
        parser.error("--project-root (or CUK_ROBOTICS_ROOT) must point to smolvla-bimanual-manipulation "
                     f"with scripts/run_robot.py; got {options.project_root}")
    # infer는 로봇 저장소를 작업 폴더로 삼아 돈다. 상대 경로를 그대로 두면 그 안에서 또 상대로 풀려 두 번 붙는다.
    options.project_root = Path(options.project_root).resolve()
    if options.policy_path is None or options.dataset_root is None:
        parser.error(f"{options.mode} requires --policy-path and --dataset-root.")
    if options.fps is not None and not 1 <= options.fps <= 120:
        parser.error("--fps must be between 1 and 120.")
    if options.duration_s is not None and (not math.isfinite(options.duration_s) or options.duration_s <= 0):
        parser.error("--duration-s must be finite and positive.")
    if options.end_detector is not None and options.robot_protocol != "task_list":
        parser.error("--end-detector is only used by --robot-protocol task_list.")
    for mode, names in MODE_ONLY.items():
        if mode != options.mode:
            given = [name for name in names if getattr(options, name) is not None]
            if given:
                parser.error(f"{', '.join('--' + n.replace('_', '-') for n in given)} is only available for {mode}.")
    if options.mode == "run":
        if options.task is None:
            parser.error("run requires --task.")
        try:
            options.task = resolve_task(options.task)
            if options.duration_s is not None:
                options.duration_s = check_manual_duration(options.duration_s)
        except ValueError as error:
            parser.error(str(error))
    for name, value in MODE_DEFAULTS[options.mode].items():
        if getattr(options, name) is None:
            setattr(options, name, value)
    if options.mode == "phone":
        options.stt_provider = options.stt_provider or default_provider()
        options.stt_model = options.stt_model or PROVIDERS[options.stt_provider]["model"]


def run_manual(options):
    """run 모드. task 하나를 infer로 바로 보낸다. 이벤트는 그대로 콘솔에 찍는다."""
    steps = plan([options.task], options.duration_s)
    print(f"[manual] ({steps[0][1]:g}s): {options.task}", flush=True)
    session = launch(options, steps, "manual",
                     lambda event: print("BEVERAGE " + json.dumps(event, ensure_ascii=False), flush=True))
    result = session.wait()
    if result not in ("complete", "stop"):
        raise SystemExit(f"infer ended with {result}")


def main(argv=None):
    load_env_file()  # 옵션 기본값이 환경 변수를 읽으니 파서보다 먼저
    parser = build_parser()
    options, extra = parser.parse_known_args(argv)
    options.infer_extra = extra  # 모르는 옵션은 infer로 넘긴다
    validate_options(parser, options)
    if options.mode == "phone":
        from .phone import run_phone
        run_phone(options)
    else:
        run_manual(options)


if __name__ == "__main__":
    main()
