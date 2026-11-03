# 사용법

1. [두 화면](#1-두-화면)
2. [음성 인식](#2-음성-인식)
3. [음료와 task 문장](#3-음료와-task-문장)
4. [명령과 옵션](#4-명령과-옵션)
5. [HTTP API](#5-http-api)
6. [실행 기록](#6-실행-기록)
7. [한계](#7-한계)

## 1. 두 화면

### 사용자 화면 `/`

음성 하나뿐입니다. 아래쪽 파란 버튼을 누른 채 말하고 손을 떼면 PC가 받아 적어 음료를 시작합니다. 화면에는 들은 말, 몇 단계째인지, 끝났는지가 나옵니다.

- 로봇이 쉬고 있을 때만 주문을 받습니다.
- 한 잔이 끝나면 어떻게 끝났든 "준비 중"이 됩니다. 팀이 작업대를 정리하고 팀 화면에서 **주문 받기**를 누를 때까지 새 주문을 받지 않습니다.
- 한 문장에 음료가 둘 들리면("커피 말고 말차") 시작하지 않고 한 잔씩 말해 달라고 합니다.

### 팀 화면 `/team`

팀 화면의 버튼이 최우선입니다. 리스트가 도는 중에 누르면 리스트를 버리고 그 동작 하나만 합니다.

| 버튼 | 하는 일 |
| --- | --- |
| 중지 | 지금 task를 끝내고 시작 자세로 돌아간 뒤 연결을 끊습니다. 비상정지를 대신하지 않습니다 |
| 초기 위치로 이동 | 돌고 있으면 중지와 같습니다. 쉬고 있으면 로봇 저장소의 `home`으로 팔을 초기 자세에 보냅니다 |
| 주문 받기 | "준비 중"을 풀어 사용자 화면이 다시 주문을 받게 합니다 |
| 음료 만들기 | 커피, 아이스티, 말차 리스트를 시작합니다. 음성이 안 될 때 씁니다 |
| task 하나만 | 가루 3종, 물, 젓기 중 하나. 리스트가 도는 중이면 끊고 이것만 합니다. 상한 칸을 비우면 기본값입니다 |
| 자세히 | 로봇 쪽 마지막 출력, 서버 설정, 실행한 명령, 최근 기록 |

팀 토큰으로는 사용자 화면도 열립니다. 사용자 화면 토큰으로는 팀 기능을 쓸 수 없습니다.

로봇이 멈추는 중이거나 복귀하는 중에는 새 주문, 끼어들기, 초기 위치 이동을 받지 않습니다. 로봇 프로세스가 장치를 완전히 놓은 뒤에 "준비 중"으로 바뀝니다.

## 2. 음성 인식

녹음은 PC가 받아 [Groq](https://console.groq.com)의 Whisper(`whisper-large-v3`)로 받아 적습니다. 키가 PC에만 있고 폰으로 넘어가지 않습니다. `GROQ_API_KEY` 대신 `OPENAI_API_KEY`를 두면 OpenAI의 Whisper를 씁니다.

이름을 조금 틀리게 적어도 알아듣도록 세 단계로 봅니다.

1. **힌트.** 전사 요청에 "말차, 아이스티, 커피 중 하나를 말한다"는 힌트를 같이 보냅니다. 비슷한 소리를 처음부터 그 표기로 적을 가능성이 높아집니다.
2. **표기 변형.** 자주 틀리는 표기와 영어 표기를 그대로 받습니다. 공백, 대소문자, 문장부호는 무시합니다.
3. **비슷한 발음.** 그래도 없으면 같은 글자 수 구간을 자음·모음으로 풀어 한 자모까지 다른 것을 받습니다. 첫 자음이 다르면 받지 않아서 "알차게", "발차"에는 반응하지 않습니다. "녹초"처럼 실제 낱말인 것은 따로 뺐습니다.

| 음료 | 알아듣는 말 (예) |
| --- | --- |
| 말차 | 말차, 녹차, 그린티, matcha, 맛차, 말챠, 마차, 말자, 막차, 녹자 라떼, 그린피 |
| 아이스티 | 아이스티, iced tea, 아이스T, 아이스띠, 아이스치, 아이쓰티 |
| 커피 | 커피, 아메리카노, coffee, 코피, 꺼피, 커삐, 카피, 커비 |

넓힐수록 잡담에 반응할 위험도 커집니다. 그래서 버튼을 누르는 동안만 녹음하고, 로봇이 쉴 때만 주문을 받고, 음료가 둘 들리면 시작하지 않습니다. 콘솔에 `Phone: '들은 문장' -> [음료]` 줄이 찍히니, 현장에서 놓친 표기를 모아 `beverage_voice/tasks.py`의 `KEYWORDS`, `FUZZY_KEYWORDS`, `FUZZY_IGNORE`에 넣으면 됩니다.

## 3. 음료와 task 문장

| 음료 | 키 | 도는 순서 |
| --- | --- | --- |
| 말차 | `greentea` | 가루 → 물 → 젓기 |
| 아이스티 | `icetea` | 가루 → 물. 젓기 학습 데이터가 없어 뺐습니다 |
| 커피 | `coffee` | 가루 → 물 → 젓기 |

| 단계 이름 | task 문장 | 상한 |
| --- | --- | --- |
| `powder_coffee` | `Pour coffee powder from the red-lidded container into the cup.` | 100초 |
| `powder_icetea` | `Pour iced tea powder from the blue-lidded container into the cup.` | 100초 |
| `powder_greentea` | `Pour green tea latte powder from the green-lidded container into the cup.` | 100초 |
| `water` | `Pour water into the cup.` | 75초 |
| `stir` | `Stir the mixture in the cup with the muddler.` | 90초 |

- 문장은 로봇 저장소 `docs/recording.md`에서 옮겼고, 공개 통합 데이터셋 5개의 `meta/tasks.parquet`와 글자 하나까지 같은 것을 확인했습니다. 다른 데이터셋으로 파인튜닝했다면 로봇 쪽 infer가 없는 문장을 경고합니다.
- 단계 전환은 end detector가 합니다. 팔이 종료 자세에 2초 머물면 다음 문장으로 넘어갑니다.
- 상한은 detector가 울리지 않을 때의 안전장치입니다. 수집 평균(가루 약 46초, 물 37초, 젓기 45초)의 두 배쯤이고, 실물에서 잰 값으로 다시 맞춥니다.
- detector를 쓰는데 상한이 지나도록 종료 자세에 못 돌아오면, 다음 단계로 넘어가지 않고 멈춥니다. detector 없이 띄우면 상한마다 다음 단계로 넘어갑니다.
- 문장, 상한, 키워드를 고칠 곳은 `beverage_voice/tasks.py` 한 군데입니다.

## 4. 명령과 옵션

```text
python -m beverage_voice phone [옵션]             폰 서버
python -m beverage_voice run --task water [옵션]   PC에서 task 하나
python -m unittest discover -s tests              검사
```

infer로 그대로 넘어가는 옵션입니다. 이름과 뜻이 로봇 저장소 cli와 같습니다.

| 옵션 | 뜻 |
| --- | --- |
| `--project-root` | 로봇 저장소 루트. 기본은 `CUK_ROBOTICS_ROOT` |
| `--policy-path`, `--dataset-root` | 정책 폴더, 파인튜닝 데이터셋 |
| `--task` | run 전용. 단계 이름이나 task 문장 |
| `--duration-s` | 모든 task의 상한을 이 값으로. run은 1~300초 |
| `--device` | `cuda`(기본) 또는 `cpu` |
| `--fps` | 제어 주기. 기본은 데이터셋 fps |
| `--wrist-fourcc` | 손목 카메라 형식. 기본 `MJPG` |
| `--no-target-limit` | 양팔 목표 차이 제한을 이번 실행에서만 끕니다 |
| `--no-return-to-initial-position` | 끝나거나 끼어들 때 시작 자세로 돌아가지 않습니다. 기본은 돌아갑니다 |
| `--validate-only` | 설정만 검사하고 모터는 연결하지 않습니다 |
| 그 밖 | 여기 없는 옵션(`--rerun-port` 등)은 그대로 infer로 넘어갑니다 |

이 서버만 쓰는 옵션입니다.

| 옵션 | 뜻 |
| --- | --- |
| `--end-detector` | beverage-autonomy `episode_end_detector/config.json` |
| `--dry-run` | 로봇을 움직이지 않고 흐름만 봅니다. 로봇 저장소와 정책 경로가 없어도 됩니다 |
| `--stt-provider` | `groq` 또는 `openai`. 기본은 `GROQ_API_KEY`가 있으면 `groq` |
| `--stt-model` | 전사 모델. 기본은 Groq `whisper-large-v3`, OpenAI `whisper-1`. Groq `whisper-large-v3-turbo`는 더 빠르고 한국어는 조금 덜 정확합니다 |
| `--host`, `--port` | 기본 `127.0.0.1`, `8000` |
| `--token`, `--team-token` | 화면 토큰. 기본은 `BEVERAGE_DEMO_TOKEN`, `BEVERAGE_TEAM_TOKEN` |
| `--python` | `run_robot.py`를 돌릴 파이썬. 기본은 `CUK_ROBOTICS_PYTHON` |
| `--robot-protocol single` | 패치 전 로봇 저장소용. task마다 infer를 새로 띄워 정책을 매번 다시 올립니다. 패치가 없으면 윈도우에서 중지할 때 복귀 없이 프로세스가 끊기고, 초기 위치 버튼은 `home` 모드가 없어 실패합니다 |

"말차 타"를 받으면 로봇 저장소 루트에서 이 명령 하나가 돕니다. 형식은 [protocol.md](protocol.md)에 있습니다.

```text
python scripts/run_robot.py infer --policy-path <정책> --dataset-root <데이터셋> --device cuda --wrist-fourcc MJPG --return-to-initial-position
  --tasks ["Pour green tea latte powder from the green-lidded container into the cup.", "Pour water into the cup.", "Stir the mixture in the cup with the muddler."]
  --task-durations [100.0, 75.0, 90.0] --end-detector <config.json>
```

## 5. HTTP API

화면이 쓰는 것과 같은 요청입니다. 토큰은 `X-Token` 헤더로 보냅니다. 주소의 `?token=`은 화면(`/`, `/team`)을 열 때만 씁니다.

| 요청 | 토큰 | 하는 일 |
| --- | --- | --- |
| `POST /speak` | 사용자 | 본문은 녹음 파일 그대로(`Content-Type: audio/webm` 등). 받아 적고 주문 |
| `POST /command` | 사용자 | `{"text": "말차 타"}`. 받아 적은 문장을 직접 넣어 주문 |
| `GET /status` | 사용자 | 지금 상태 |
| `POST /infer` | 팀 | `{"task": "water", "duration_s": 45}`. task 하나, 돌고 있으면 끼어들기 |
| `POST /stop` | 팀 | 중지 |
| `POST /home` | 팀 | 초기 위치로 이동 |
| `POST /resume` | 팀 | 주문 받기 |
| `GET /debug` | 팀 | 로봇 쪽 마지막 출력, 설정, 실행한 명령 |

## 6. 실행 기록

실행마다 `runs.jsonl`에 task별 종료 이유와 시간이 한 줄씩 남습니다. 성공 여부는 자동으로 판정하지 않으니 사람이 그 줄의 시각을 키로 따로 적습니다. 이 파일은 저장소에 올라가지 않습니다.

## 7. 한계

- 음성 인식에는 인터넷과 Groq 키가 필요합니다. 무료 등급 한도를 넘거나 인터넷이 끊기면 사용자 화면에 안내가 뜨고, 팀 화면 버튼은 그대로 동작합니다.
- Groq의 인식 처리 자체는 0.5초 안팎입니다. 다만 네트워크에 따라 연결이 매달릴 수 있습니다. 개발 PC에서는 TCP 연결 넷 중 둘이 21초 만에 실패해서, 한 번 말하는 데 0.5초에서 28초까지 걸렸고, 스무 번 중 한 번은 연결이 끝내 안 돼 실패했습니다. 서버는 6초 동안 답이 없으면 새 연결로 한 번 더 시도하고, 그래도 안 되면 24초 안에 화면에 안내를 보냅니다. 사용자 화면은 30초까지 기다립니다. 행사장 네트워크에서 여러 번 말해 보고 응답 시간을 확인합니다.
- 음성 인식이 소음을 엉뚱한 단어로 바꿀 수 있습니다. 버튼을 누르고 있는 동안만 녹음하므로 아무 때나 반응하지는 않습니다.
- 소프트웨어 중지는 비상정지가 아닙니다. 복귀와 초기 위치 이동에는 충돌 회피가 없어, 물체를 쥔 채 멈추면 떨어뜨릴 수 있습니다.
