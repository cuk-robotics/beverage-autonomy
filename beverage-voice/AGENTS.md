# 에이전트용 안내

이 폴더는 폰 음성 주문을 SO-101 양팔 로봇의 SmolVLA 추론으로 넘기는 서버입니다. 로봇 제어는 smolvla-bimanual-manipulation의 `scripts/run_robot.py`가 하고, 이 폴더는 그 명령을 띄우고 표준 입력과 출력으로 대화합니다. 사람용 설명은 `README.md`와 `docs/setup.md`, `docs/usage.md`, 로봇 쪽 작업은 `robot_side/README.md`, 두 쪽 형식은 `docs/protocol.md`에 있습니다.

## 로봇 PC에 붙이는 순서

각 단계의 확인 명령이 통과해야 다음으로 갑니다. 모터를 움직이는 명령은 사람이 하게 두고, 에이전트는 `--validate-only`와 단위 검사까지만 돌립니다.

1. 로봇 저장소와 beverage-autonomy 위치를 확인합니다. 로봇 저장소에는 `configs/local/hardware.json`과 보정 파일이 이미 있어야 하고, `infer --task ... --validate-only`가 지금 통과해야 합니다. 안 되면 그 저장소 문제이니 여기서 멈추고 사람에게 알립니다.
2. 패치를 붙입니다. `git apply --check`가 실패하면 아래 "패치가 안 붙을 때"를 따릅니다.
   ```powershell
   git -C <로봇 저장소> apply <이 폴더>/robot_side/smolvla-bimanual-manipulation.patch
   git -C <beverage-autonomy> apply <이 폴더>/robot_side/beverage-autonomy.patch
   ```
3. 이 폴더의 `.env.example`을 `.env`로 복사해 `CUK_ROBOTICS_ROOT`를 채웁니다. 키와 토큰은 사람이 넣습니다. 이미 `.env`가 있으면 건드리지 않습니다.
   `robot_side/home.example.json`을 로봇 저장소 `configs/local/home.json`으로 복사합니다. 이미 있으면 덮어쓰지 말고 사람에게 묻습니다.
4. 확인합니다.
   ```powershell
   python -m unittest discover -s tests                     # 로봇 저장소. 새 test_task_list.py 포함 전부 통과
   python -m unittest discover -s episode_end_detector/tests  # beverage-autonomy
   python -m unittest discover -s tests                     # 이 폴더. 전부 통과
   python scripts/run_robot.py home --validate-only         # 로봇 저장소
   python -m beverage_voice run --task water --validate-only --project-root <로봇 저장소> --policy-path <정책> --dataset-root <데이터셋> --end-detector <beverage-autonomy>/episode_end_detector/config.json
   ```
   마지막 명령의 출력에 `Task 1/1: Pour water into the cup.`과 `Validated official rollout configuration`이 있어야 합니다. `WARNING: not in .../tasks.parquet`이 나오면 `--dataset-root`가 파인튜닝 데이터셋인지 사람에게 확인합니다.
5. 사람에게 넘깁니다. 실물 확인 순서는 `robot_side/README.md` 4절입니다.

## 패치가 안 붙을 때

패치는 2026-11-03 GitHub main 기준입니다. 그 사이 main이 바뀌었으면 `git apply --3way`를 먼저 해 보고, 안 되면 패치를 읽고 같은 뜻으로 옮깁니다. 지켜야 할 것은 이렇습니다.

- 새 파일 `task_list.py`, `home.py`, `tests/test_task_list.py`는 그대로 복사해도 됩니다. 다른 모듈에 기대는 곳이 없습니다.
- `cli.py`: mode 선택지에 `home`을 넣고, `--tasks`, `--task-durations`, `--end-detector`를 받습니다. `--tasks`가 있으면 `parse_plan()` 결과를 `options.task_plan`에, 첫 문장을 `options.task`에 넣습니다. `home`은 카메라 레이아웃 설치보다 먼저 처리합니다.
- `inference.py`: `options.task_plan`이 있으면 rollout `--duration`을 상한의 합으로 주고, `task_list_strategy(official, plan, detector)`를 `rollout_warmup`과 같은 `with`에 겁니다. `--validate-only`에서도 detector를 읽고 계획을 출력합니다.
- LeRobot 버전이 0.6.1이 아니면 `task_list.py`가 기대는 이름(`SyncInferenceEngine._task`, `BaseStrategy`의 `_process_observation_and_notify`, `_handle_warmup`, `_log_telemetry`, `_return_to_initial_position`, `lerobot_rollout.create_strategy`)이 남아 있는지 먼저 봅니다.

## 지킬 것

- `docs/protocol.md`의 형식을 바꾸면 로봇 쪽 `task_list.py`, 이 폴더 `beverage_voice/inference.py`, 가짜 로봇 `tests/fake_robot/scripts/run_robot.py`를 같이 고칩니다.
- 정책에 넘기는 문장은 `beverage_voice/tasks.py`의 다섯 개뿐입니다. 학습 데이터 `meta/tasks.parquet`와 글자 하나까지 같아야 합니다. 폰에서 임의 문장이나 infer 옵션을 받게 만들지 않습니다.
- `GROQ_API_KEY`(또는 `OPENAI_API_KEY`)와 화면 토큰은 환경 변수나 이 폴더의 `.env`로만 받습니다. `.env`는 커밋하지 않고, 키를 저장소, 로그, 커밋 메시지에 넣지 않습니다. 토큰이 붙은 주소는 로봇을 움직이니 출력에 남기지 않습니다.
- 개인 경로, 기기 이름, 테일넷 주소를 문서에 쓰지 않습니다. 예시는 `C:\path\to\...`, `<PC 이름>.<테일넷>.ts.net`처럼 씁니다.
- `configs/local/`, 보정 파일, `runs.jsonl`은 커밋하지 않습니다.
- 소프트웨어 중지는 비상정지가 아닙니다. 문서나 화면 문구에서 그렇게 말하지 않습니다.

## 구조

| 경로 | 내용 |
| --- | --- |
| `beverage_voice/cli.py` | 진입점 `python -m beverage_voice phone`, `run`. 옵션 이름은 로봇 cli와 같다 |
| `beverage_voice/tasks.py` | task 문장, 상한 시간, 음성 키워드 |
| `beverage_voice/inference.py` | `run_robot.py` 실행과 프로토콜. `Session`, `DryRunSession` |
| `beverage_voice/phone.py` | HTTP 서버, 토큰, 상태, 주문·중지·끼어들기·초기 위치 |
| `beverage_voice/stt.py` | Whisper 전사 API. Groq(기본) 또는 OpenAI |
| `beverage_voice/web/` | 사용자 화면 `demo.html`, 팀 화면 `team.html`. 외부 리소스 없음 |
| `robot_side/` | 로봇 저장소와 beverage-autonomy 패치, 초기 자세 파일 |
| `docs/` | `setup.md` 설치, `usage.md` 사용법, `protocol.md` 로봇과의 규격 |
| `tests/` | 표준 라이브러리 `unittest`. 가짜 로봇을 실제 자식 프로세스로 띄운다 |
| `.env.example` | 설정 견본. 복사한 `.env`는 `.gitignore`에 있다 |
| `.github/workflows/tests.yml` | 푸시마다 Windows, Python 3.12에서 `tests/`를 돌린다 |

외부 패키지는 쓰지 않습니다. Python 3.12 이상 표준 라이브러리만으로 돕니다. `beverage_voice/` 안은 상대 import만 쓰므로 폴더째 다른 저장소로 옮겨도 됩니다.
