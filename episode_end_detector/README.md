# 에피소드 종료 탐지기

로봇 관절값으로 단계 종료를 판단하고, 종료할 때마다 다음 지시어를 반환하는 Python 모듈입니다. 모든 지시어가 끝나면 판단을 중지하며, 새 `start_episode()` 명령으로 다시 시작합니다.

Python 3.12에서 검증했습니다. Python 표준 라이브러리만 사용하므로 별도 패키지 설치, 학습 모델, 원본 데이터셋이 필요하지 않습니다. 실제 관절값 수신과 로봇 제어 명령 실행은 사용하는 프로그램에서 연결합니다.

## 파일 구성

```text
episode_end_detector/
├── .gitignore
├── README.md
├── __init__.py
├── episode_end_detector.py
├── config.json
├── instructions.json
├── range.json
├── example.py
└── tests/
    └── test_detector.py
```

| 파일 | 역할 |
| --- | --- |
| `episode_end_detector.py` | 종료 판정과 지시어 순서 관리 |
| `config.json` | 범위·지시어 파일 경로와 시간 조건 |
| `instructions.json` | 순서대로 수행할 지시어 목록 |
| `range.json` | 관절 이름·순서와 종료 범위의 하한·상한 |
| `example.py` | JSON Lines 입력을 받는 실행 예시 |
| `tests/test_detector.py` | 외부 패키지 없이 실행하는 동작 테스트 |

폴더 전체를 저장소 루트로 올리거나 다른 프로젝트에 복사할 수 있습니다. `.gitignore`는 Python 캐시, 가상환경, IDE 설정을 제외합니다.

## 빠른 실행

이 폴더 안에서 실행합니다.

```sh
python example.py
```

표준 입력에 JSON 객체를 한 줄씩 전달합니다. 먼저 시작 명령을 입력하면 첫 지시어를 `current_instruction`에 반환합니다.

```json
{"command":"start_episode"}
```

이후 관측값을 입력합니다. 아래 값은 형식 예시이며 실제 관절값으로 바꿔야 합니다.

```json
{"command":"observation","timestamp":0.0,"state":[0,0,0,0,0,0,0,0,0,0,0,0]}
```

각 입력에 대한 결과를 표준 출력으로 한 줄씩 반환합니다. 단계 종료 시 `next_instruction`에 다음 지시어가 들어갑니다. 마지막 단계 종료 시 `sequence_done`이 `true`가 됩니다. 잘못된 입력은 `error` 응답을 반환하고 다음 줄을 기다립니다.

파일 입력도 가능합니다. PowerShell에서는 다음처럼 실행합니다.

```powershell
Get-Content observations.jsonl | python example.py
```

## 로봇 제어 코드에 연결

폴더 상위 경로에서 패키지로 사용하거나, 폴더 내부에서 모듈로 사용해도 같은 import를 사용합니다.

```python
from episode_end_detector import EpisodeEndDetector

detector = EpisodeEndDetector.load_config()

def on_start_episode():
    first_instruction = detector.start_episode()
    print(first_instruction)
    return first_instruction

def on_joint_observation(joint_positions):
    result = detector.update(joint_positions)
    if result["next_instruction"] is not None:
        print(result["next_instruction"])
    if result["event"] == "episode_end" and result["sequence_done"]:
        print("전체 지시어 완료")
    return result
```

외부 시작 명령 수신 시 `on_start_episode()`, 관절값 수신 시 `on_joint_observation()`을 호출합니다. 출력한 지시어를 로봇 정책에 전달하는 부분은 사용하는 프로그램에서 연결합니다. 단계 사이에 `start_episode()`를 다시 호출하면 목록이 처음으로 돌아가므로 자동 전환을 그대로 사용합니다.

## 설정 변경

`config.json` 기본값:

```json
{
  "range_file": "range.json",
  "instructions_file": "instructions.json",
  "hold_seconds": 2.0,
  "max_gap_seconds": 0.2
}
```

- `hold_seconds`: 종료 범위 안에서 연속 유지해야 하는 시간(초).
- `max_gap_seconds`: 연속 관측으로 인정하는 최대 간격(초). 초과하면 체류 시간을 초기화합니다.
- 파일 경로는 설정 파일이 있는 폴더 기준입니다. 작업 디렉터리를 바꿔도 기본 설정을 읽을 수 있습니다.
- 다른 설정 파일은 `EpisodeEndDetector.load_config("path/to/config.json")`으로 지정합니다.

`instructions.json` 기본 목록:

```json
[
  "Pour green tea latte powder from the green-lidded container into the cup.",
  "Pour water into the cup.",
  "Stir the mixture in the cup with the muddler."
]
```

목록과 각 문자열은 비어 있으면 안 됩니다. 파일 대신 직접 리스트를 입력할 수도 있습니다.

```python
detector = EpisodeEndDetector.load_config(instructions=["first", "second"])
first_instruction = detector.start_episode()
first_instruction = detector.start_episode(["new first", "new second"])
```

실행 예시에서도 시작 명령으로 목록을 교체할 수 있습니다.

```json
{"command":"start_episode","instructions":["first","second"]}
```

## 판정 기준과 입력

1. 시작 전에는 종료 판정을 하지 않습니다.
2. 시작 후 처음으로 종료 범위를 벗어나면 판단을 활성화합니다. 시작 후 첫 관측이 범위 밖이면 즉시 활성화합니다.
3. 관절 12개가 모두 각자의 종료 범위 안에 들어오면 시간을 측정합니다. 하한과 상한도 내부에 포함합니다.
4. 연속 2초 유지하면 단계 종료 이벤트를 한 번 반환합니다. 범위 이탈이나 0.2초를 초과하는 관측 공백은 체류 시간을 초기화합니다.
5. 다음 지시어가 있으면 자동으로 최초 범위 이탈 대기로 전환합니다. 종료 자세에서 계속 대기하는 것만으로 다음 단계가 종료되지 않습니다.
6. 목록이 끝나면 관측값 검증, 범위 비교, 시간 갱신을 중지합니다. `start_episode()`가 호출되면 첫 지시어부터 다시 시작합니다.

`update(state, timestamp=None)`의 `state`는 정규화·패딩하지 않은 실제 관절값 12개입니다. 순서는 `range.json`의 `joint_names`와 같아야 하며, 단위와 로봇 보정 기준도 범위 생성 데이터와 같아야 합니다.

`timestamp`는 초 단위입니다. 생략하면 `time.monotonic()`을 사용합니다. 한 에피소드 내에서는 같은 시간 기준을 유지합니다. 데이터 재생 시에는 타임스탬프를 직접 지정합니다.

```python
result = detector.update(joint_positions, timestamp=1.25)
```

잘못된 차원, NaN·무한대, 역행하는 시간은 `ValueError`를 발생시키고 체류 시간을 초기화합니다. 복구 입력 시간은 마지막 정상 관측 시간 이상이어야 합니다. 전체 목록 완료 후에는 관측 입력을 무시합니다.

## 반환값

| 항목 | 의미 |
| --- | --- |
| `status` | `waiting_start`, `waiting_exit`, `active`, `done` |
| `in_range` | 모든 관절의 범위 포함 여부. 전체 완료 후 대기 중에는 `None` |
| `dwell_seconds` | 연속 체류 시간. 종료 시 유지 시간 값으로 고정 |
| `episode_done` | 해당 단계의 종료 여부 |
| `event` | 종료 순간에만 `"episode_end"`, 그 외에는 `None` |
| `current_instruction` | 현재 지시어. 종료 시 전환된 다음 지시어, 전체 완료 시 `None` |
| `next_instruction` | 종료 이벤트에서 새로 출력할 다음 지시어. 그 외에는 `None` |
| `sequence_done` | 전체 지시어 완료 여부 |

종료 이벤트의 `status="done"`, `episode_done=True`는 방금 끝난 단계의 결과입니다. 다음 지시어가 있으면 내부 상태는 이미 최초 이탈 대기로 전환되며, 다음 `update()`부터 새 단계 상태를 반환합니다. 지시어 출력에는 `next_instruction`, 전체 완료 알림에는 `event`와 `sequence_done`을 함께 사용합니다.

전체 완료 이후에는 `event=None`, `current_instruction=None`, `next_instruction=None`, `sequence_done=True`를 유지합니다. 자동 반복하지 않습니다.

지시어 없이 단일 단계만 판단하려면 `EpisodeEndDetector.load("range.json")`을 사용합니다. 이 경우 종료 상태는 다음 `start_episode()`까지 유지됩니다.

## 테스트

이 폴더 안에서 실행합니다.

```sh
python -m unittest discover -s tests -v
```

기본 파일 로딩, 2초 경계, 단계 전환, 전체 완료 후 대기·재시작, 관측 공백·잘못된 입력을 검증합니다.

## 제공 범위의 한계

`range.json`은 4개 음료 데이터셋의 159개 에피소드 마지막 관측값에서 관절별 2.5~97.5 백분위를 구하고 양쪽에 0.5의 여유폭을 적용한 공통 범위입니다. 원본 데이터 경로나 에피소드 목록은 포함하지 않습니다.

현재 범위와 2초 조건으로 홀드아웃 32개 에피소드를 재생했을 때 종료 검출은 0건이었습니다. 종료 자세로 돌아왔는지를 판단하는 모듈이며 실제 작업 성공을 직접 검증하지 않습니다. 적용 환경에서 범위와 유지 조건의 적합성을 확인해야 합니다.
