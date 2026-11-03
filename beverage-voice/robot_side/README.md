# 로봇 PC에 붙이기

beverage-voice가 쓰는 기능 두 개를 로봇 저장소에 넣는 방법입니다. 하나는 `infer --tasks`로 한 세션에서 가루, 물, 젓기를 이어 도는 기능이고, 다른 하나는 팔을 초기 자세로 보내는 `home` 모드입니다. 코드는 이미 패치로 만들어 두었으니 적용하고 확인만 하면 됩니다.

| 파일 | 적용할 곳 |
| --- | --- |
| `smolvla-bimanual-manipulation.patch` | 로봇 저장소 루트에서 `git apply` |
| `beverage-autonomy.patch` | beverage-autonomy 루트에서 `git apply` |
| `home.example.json` | 로봇 저장소 `configs/local/home.json`으로 복사 |

## 1. 패치 적용

두 저장소가 2026-11-03 GitHub main 상태라면 그대로 붙습니다.

```powershell
cd C:\path\to\smolvla-bimanual-manipulation
git apply --check C:\path\to\beverage-voice\robot_side\smolvla-bimanual-manipulation.patch
git apply C:\path\to\beverage-voice\robot_side\smolvla-bimanual-manipulation.patch

cd C:\path\to\beverage-autonomy
git apply C:\path\to\beverage-voice\robot_side\beverage-autonomy.patch
```

`--check`에서 막히면 그 사이 main이 바뀐 것입니다. 바뀐 부분이 작으면 `git apply --3way`로 붙여 보고, 아니면 패치를 읽고 같은 자리에 손으로 옮깁니다. 바꾸는 곳은 아래 표가 전부입니다.

**smolvla-bimanual-manipulation**

| 파일 | 바뀌는 것 |
| --- | --- |
| `src/cuk_robotics/teleoperation/task_list.py` | 새 파일. 문장 목록을 도는 rollout 전략, 표준 입력 명령, `BEVERAGE` 이벤트 |
| `src/cuk_robotics/teleoperation/home.py` | 새 파일. 팔로워 두 대를 자세 파일로 옮기는 `home` 모드. 중간에 멈춰도 연결은 끊고 나간다 |
| `src/cuk_robotics/teleoperation/cli.py` | `home` 모드, `--tasks`, `--task-durations`, `--end-detector` 옵션과 검사 |
| `src/cuk_robotics/teleoperation/inference.py` | `--tasks`가 있으면 위 전략으로 바꿔 끼움. 윈도우 SIGBREAK를 Ctrl+C로 받음 |
| `tests/test_task_list.py` | 장치 없는 검사 8개 |
| `README.md`, `docs/inference.md`, `docs/safety.md` | 새 옵션과 `home` 설명, 주의 한 줄 |

**beverage-autonomy**

| 파일 | 바뀌는 것 |
| --- | --- |
| `episode_end_detector/range.json` | 젓기까지 들어간 새 종료 범위 (아래 5절) |
| `episode_end_detector/config.json` | `max_gap_seconds` 0.2 → 1.0 |
| `episode_end_detector/tests/test_detector.py`, `README.md` | 위 두 값에 맞춤 |

기존 `infer --task ...`는 그대로 동작합니다. `--tasks`를 줄 때만 새 전략을 씁니다.

## 2. 초기 자세 파일

```powershell
copy C:\path\to\beverage-voice\robot_side\home.example.json configs\local\home.json
```

값은 수집 데이터의 휴식 자세이고, 보정 파일이 수집 때와 같다는 전제입니다. 보정을 다시 했다면 팔을 수집 시작 자세에 두고 `operation`이나 preview로 관절값을 읽어 이 표와 몇 도 안에서 맞는지 본 뒤 씁니다.

| 관절 | 값 | 관절 | 값 |
| --- | --- | --- | --- |
| left_shoulder_pan | -5.7 | right_shoulder_pan | -0.2 |
| left_shoulder_lift | -101.0 | right_shoulder_lift | -100.9 |
| left_elbow_flex | 99.7 | right_elbow_flex | 91.6 |
| left_wrist_flex | 56.7 | right_wrist_flex | 68.1 |
| left_wrist_roll | 29.1 | right_wrist_roll | 24.9 |
| left_gripper | 0.9 | right_gripper | 1.6 |

## 3. 장치 없이 확인

로봇 저장소에서 아래 세 가지가 통과하면 beverage-voice와 맞물린 것입니다. 두 경로는 로봇 저장소 `docs/inference.md`와 같은 뜻입니다.

```powershell
$policy  = 'models/YOUR_PRETRAINED_MODEL'
$dataset = 'data/lerobot/YOUR_DATASET'    # 파인튜닝에 쓴 데이터셋
python -m unittest discover -s tests
python scripts/run_robot.py home --validate-only
python scripts/run_robot.py infer --validate-only --policy-path $policy --dataset-root $dataset --wrist-fourcc MJPG `
  --tasks '[\"Pour water into the cup.\"]' --task-durations '[75]' --end-detector C:\path\to\beverage-autonomy\episode_end_detector\config.json
```

Windows PowerShell 5.1은 외부 프로그램 인자에서 `"`를 지우므로 JSON 안의 따옴표를 `\"`로 씁니다. PowerShell 7.3 이상이면 `\` 없이 `'["Pour water into the cup."]'`로 씁니다. beverage-voice가 띄울 때는 인자를 목록으로 넘겨서 이 문제가 없습니다.

마지막 명령은 `Task 1/1: ...`, `End detector: ...`, `Validated ...` 줄로 끝나야 합니다. 문장이 데이터셋 `meta/tasks.parquet`에 없으면 `WARNING: not in ...` 줄이 나옵니다. 그때는 파인튜닝에 쓴 데이터셋을 `--dataset-root`로 주었는지부터 봅니다.

그다음 beverage-voice에서 서버를 띄웁니다([docs/setup.md](../docs/setup.md#3-서버-띄우기)). 시작할 때 위와 같은 검사를 스스로 한 번 돌립니다.

```powershell
python -m beverage_voice phone --policy-path $policy --dataset-root $dataset --end-detector C:\path\to\beverage-autonomy\episode_end_detector\config.json
```

## 4. 실물에서 확인하는 순서

모터가 움직이는 단계입니다. 물리 비상정지 담당을 먼저 정합니다. 아래 `run_robot.py`는 로봇 저장소에서, `beverage_voice`는 beverage-voice 폴더에서 실행합니다. `CUK_ROBOTICS_ROOT`는 [docs/setup.md](../docs/setup.md#2-설정-파일-env)처럼 `.env`에 넣어 둡니다.

```powershell
$eed = 'C:\path\to\beverage-autonomy\episode_end_detector\config.json'
python scripts/run_robot.py home                                                                                    # 1
python -m beverage_voice run --task powder_coffee --duration-s 150 --policy-path $policy --dataset-root $dataset     # 2
python -m beverage_voice run --task powder_coffee --end-detector $eed --policy-path $policy --dataset-root $dataset  # 3
```

1. **초기 자세.** 팔을 손으로 수집 시작 자세 근처에 두고 `home`을 돌립니다. 거의 움직이지 않아야 정상입니다. 관절 하나를 20도쯤 옮겨 두고 다시 돌려 표의 값으로 돌아오는지, 작업대와 부딪히지 않는지 봅니다.
2. **소요 시간.** detector 없이 task 하나씩 상한 150초로 돌리고 눈으로 본 완료 시각을 적습니다. 수집 평균은 가루 46초, 물 37초, 젓기 45초였습니다. beverage-voice의 상한 기본값 100/75/90초를 잰 값의 두 배쯤으로 맞춥니다.
3. **detector.** 가루 task 하나를 `--end-detector`와 함께 돌려 `task_end` 줄에 `"reason": "detector"`가 몇 초에 찍히는지 봅니다.
4. **중지.** 가루 task 중간에 팀 화면의 중지를 눌러 복귀와 연결 해제가 되는지 봅니다.
5. **끼어들기.** 리스트가 도는 중에 팀 화면에서 물을 눌러 지금 task가 끊기고 물 하나만 돈 뒤 끝나는지 봅니다.
6. **한 잔 전체.** 폰으로 "말차 타"를 말해 세 문장이 사람 손 없이 이어지는지 봅니다.
7. **목표 차이 제한.** 같은 task를 `--no-target-limit` 있고 없고로 돌려 상한 안에 끝나는지, 동작이 튀는지 비교합니다. 제한은 매 틱 목표를 현재 위치 근처로 자르는 속도 상한이라 동작을 느리게 만들 수 있습니다.
8. **top 카메라.** 젓기 데이터는 top 영상이 raw와 masked 두 종류입니다. 파인튜닝에 넣은 쪽과 현장 OBS 장면이 같은지 봅니다.

복귀와 `home`에는 충돌 회피가 없습니다. 물체를 쥔 채 멈추거나 끼어들면 복귀 중에 떨어뜨릴 수 있습니다.

## 5. 왜 이렇게 만들었나

**task 리스트.** LeRobot 0.6.1의 동기 추론 엔진은 task 문장을 `_task` 속성 하나로 들고 매 틱 관측에 붙입니다. 그래서 정책과 장치는 그대로 두고 문장만 바꾸면 됩니다. 문장을 바꿀 때 `engine.reset()`으로 앞 문장의 action 묶음을 버립니다. 루프 본체는 공식 `base` 전략과 같고, 끝나는 조건만 detector, 상한 시간, 끼어들기 셋이 더해졌습니다. 공식 `create_strategy`는 이름별 if 분기라서, 로봇 저장소가 워밍업을 끼울 때처럼 `lerobot_rollout.create_strategy`를 실행하는 동안만 바꿔 끼웁니다. 설정은 `--strategy.type=base` 그대로입니다.

**중지.** `stop`은 공식 루프가 보는 `shutdown_event`를 켭니다. Ctrl+C와 같은 종료 경로라 teardown이 시작 자세로 3초 선형 복귀한 뒤 연결을 끊습니다. LeRobot은 SIGINT와 SIGTERM만 받고 윈도우는 다른 프로세스에 SIGINT를 보낼 수 없어서, 표준 입력으로 받습니다.

**초기 자세.** 공개 수집 데이터 209회차(가루 105, 물 54, 젓기 50)의 첫 프레임과 마지막 프레임을 읽었습니다. 모든 task가 같은 휴식 자세에서 시작해 같은 자세로 끝나고, 시작과 끝의 평균 차이는 관절마다 1도 안쪽입니다. 회차 사이 표준편차는 손목 roll만 5~8도이고 나머지는 4도 아래입니다. 첫 프레임의 관절별 중앙값이 `home.example.json`입니다. `home`은 연결할 때 보정을 시작하지 않습니다. 보정이 파일과 다르면 움직이지 않고 오류로 끝납니다. 원격 버튼에서 대화형 보정이 뜨면 안 되기 때문입니다.

**종료 범위.** 원래 `range.json`은 가루와 물 159회차의 마지막 관측으로 만들어 젓기 종료 자세를 하나도 포함하지 못했습니다. 젓기 회차는 `left_shoulder_lift`가 -97.5도 근처인데 범위가 -102.3 ~ -99.9였습니다. 위 418개 관측을 옛 범위에 넣으면 253개, 새 범위에 넣으면 389개가 들어갑니다. 새 범위는 관절별 2.5~97.5 백분위에 폭의 25%와 2도 중 큰 값을 여유로 더했습니다. 동기 추론은 action 묶음마다 루프가 잠깐 멈추는데, 그 시간이 `max_gap_seconds` 0.2초를 넘으면 체류 시간이 매번 0으로 돌아가 2초가 차지 않습니다. 그래서 1.0초로 늘렸습니다.

## 6. 이 PC에서 검사한 것

검사는 2026-11-03에 Python 3.12, LeRobot 0.6.1, draccus 0.11.6에서 했습니다. 로봇 저장소 `requirements/constraints-windows.txt`와 같은 버전입니다.

- 패치를 적용한 로봇 저장소의 검사 75개가 모두 통과했습니다. 기존 67개와 새 8개입니다. 새 검사는 실제 LeRobot `BaseStrategy` 위에서 문장 전환, 끼어들기, 중지, 상한, 표준 입력 닫힘을 봅니다.
- `infer --tasks ... --end-detector ... --validate-only`가 공식 `RolloutConfig` 파싱까지 통과했습니다. 공개 데이터셋 메타와 실제 beverage-autonomy detector를 썼습니다.
- `home --validate-only`가 실제 `BiSOFollower` 설정 파싱을 통과했습니다. 직렬 연결만 가짜로 바꾼 전체 실행에서 자세 파일 값에 도달했습니다.
- beverage-voice 서버를 이 저장소에 붙여 시작 검사와 팀 화면의 초기 위치 버튼까지 돌렸습니다. 모터가 없어 연결 실패가 팀 화면에 그대로 보이는 것까지 확인했습니다.
- 정책 가중치를 올린 추론과 실제 모터 동작은 확인하지 못했습니다. 4절이 그 부분입니다.
