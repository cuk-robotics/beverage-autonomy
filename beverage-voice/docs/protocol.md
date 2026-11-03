# beverage-voice와 로봇 저장소 사이의 약속

beverage-voice는 로봇을 직접 움직이지 않습니다. smolvla-bimanual-manipulation의 `scripts/run_robot.py`를 자식 프로세스로 띄우고, 표준 입력으로 명령을 보내고, 표준 출력에서 진행 상황을 읽습니다. 이 문서가 그 형식의 기준입니다.

형식을 바꾸면 세 곳을 같이 고칩니다.

- 로봇 쪽 `src/cuk_robotics/teleoperation/task_list.py` (패치: `robot_side/smolvla-bimanual-manipulation.patch`)
- beverage-voice `beverage_voice/inference.py`
- 가짜 로봇 `tests/fake_robot/scripts/run_robot.py`

## 1. 실행 명령

음료 하나를 주문하면 이렇게 한 번 띄웁니다. 정책 로딩과 장치 연결은 이 한 번뿐입니다.

```text
python scripts/run_robot.py infer
  --policy-path <정책> --dataset-root <데이터셋> --device cuda --wrist-fourcc MJPG --return-to-initial-position
  --tasks '["Pour green tea latte powder from the green-lidded container into the cup.", "Pour water into the cup.", "Stir the mixture in the cup with the muddler."]'
  --task-durations '[100.0, 75.0, 90.0]'
  [--end-detector <beverage-autonomy>/episode_end_detector/config.json]
```

| 옵션 | 뜻 |
| --- | --- |
| `--tasks` | task 문장 JSON 목록. `--task`, `--duration-s`와 같이 쓰지 않는다 |
| `--task-durations` | 문장마다 상한 초. 개수가 `--tasks`와 같아야 한다 |
| `--end-detector` | 있으면 팔이 종료 자세에 2초 머물 때 다음 문장으로 넘어간다 |
| `--validate-only` | 설정만 검사하고 끝낸다. beverage-voice는 서버를 띄울 때 한 번 부른다 |

팔을 초기 자세로 보낼 때는 이 명령을 씁니다. 결과는 종료 코드로만 봅니다. 0이면 성공입니다.

```text
python scripts/run_robot.py home [--duration-s 5] [--validate-only]
```

자식 프로세스에는 `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `PYTHONUTF8=1`, `PYTHONUNBUFFERED=1`을 넣습니다.

## 2. 표준 입력: beverage-voice → 로봇

한 줄에 명령 하나입니다.

| 줄 | 로봇이 하는 일 |
| --- | --- |
| `stop` | 지금 task를 끝내고 Ctrl+C와 같은 종료 경로로 간다. 시작 자세로 복귀한 뒤 연결을 끊는다 |
| `task 75 Pour water into the cup.` | 지금 task를 끊고 시작 자세로 복귀한 뒤, 남은 리스트를 버리고 이 문장 하나만 돈다. 숫자는 상한 초, 문장은 줄 끝까지 |

- 복귀는 `--return-to-initial-position`이 있을 때만 합니다. beverage-voice는 기본으로 붙이고, `--no-return-to-initial-position`으로 띄우면 빼서 그 자리에서 이어 갑니다.

- 표준 입력이 닫히면 `stop`과 같습니다. beverage-voice가 죽으면 로봇도 멈춥니다.
- 모르는 줄은 무시하고 표준 오류에 한 줄 남깁니다.

## 3. 표준 출력: 로봇 → beverage-voice

`BEVERAGE ` 다음에 JSON 하나가 오는 줄만 이벤트입니다. 나머지 줄은 beverage-voice가 콘솔에 그대로 찍습니다.

```text
BEVERAGE {"event": "ready"}
BEVERAGE {"event": "task_start", "index": 1, "total": 3, "task": "Pour ...", "source": "list"}
BEVERAGE {"event": "task_end", "index": 1, "reason": "detector", "seconds": 48.2}
BEVERAGE {"event": "task_start", "index": 1, "total": 1, "task": "Pour water into the cup.", "source": "manual"}
BEVERAGE {"event": "done", "reason": "complete"}
```

| 이벤트 | 언제 |
| --- | --- |
| `ready` | 정책을 올리고 장치를 연결해 첫 task 직전 |
| `task_start` | 문장 하나 시작. `source`는 `list`(주문 리스트) 또는 `manual`(팀이 끼워 넣은 task) |
| `task_end` | 문장 하나 끝 |
| `done` | 세션 끝. 마지막 줄 |

`task_end.reason`

| 값 | 뜻 | 다음 |
| --- | --- | --- |
| `detector` | 종료 자세에 2초 머묾 | 다음 문장 |
| `duration` | 상한 시간이 지남 | detector가 있으면 멈춘다(`done timeout`). 없으면 다음 문장 |
| `preempted` | `task` 명령이 들어옴 | 복귀한 뒤(옵션이 있을 때) 그 문장 하나 |
| `stop` | `stop` 명령이나 Ctrl+C | 끝 |

`done.reason`은 `complete`(다 돌았음), `timeout`(종료 자세에 못 돌아와 멈춤), `stop`(중지 요청) 셋입니다. 셋 다 종료 코드는 0이고, 오류면 0이 아닌 종료 코드로 끝납니다.

로봇은 `done`을 찍은 뒤에도 복귀와 연결 해제를 합니다. 그래서 beverage-voice는 `done`을 받아 두었다가 프로세스가 끝난 뒤에 화면에 알립니다. 그 전에는 새 주문이나 초기 위치 이동을 받지 않으므로, 앞 세션이 직렬 포트를 놓기 전에 다음 세션이 뜨지 않습니다. `done` 뒤 종료 코드가 0이 아니면 정리 중 오류로 보고, `done` 없이 끝난 프로세스도 오류로 봅니다. 두 경우 모두 표준 오류 마지막 줄들을 팀 화면에 보여 줍니다.

## 4. 옛 infer와 쓰는 호환 모드

로봇 저장소에 패치를 넣기 전이라면 beverage-voice를 `--robot-protocol single`로 띄웁니다. task마다 `infer --task <문장> --duration-s <초>`를 새로 띄우고 이벤트는 beverage-voice가 만들어 냅니다. 중지는 윈도우에서 `CTRL_BREAK_EVENT`, 그 밖에서는 `SIGINT`입니다. 패치가 SIGBREAK를 Ctrl+C로 바꿔 받으므로, 패치 없이 윈도우에서 중지하면 복귀 없이 프로세스가 끊깁니다.
