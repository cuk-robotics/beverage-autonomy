# beverage-voice

폰에 대고 "말차 타"라고 말하면 SO-101 양팔 로봇이 말차 가루 넣기, 물 넣기, 젓기를 차례로 합니다.

정책은 task 문장만 다르게 해서 파인튜닝한 SmolVLA 하나입니다. 이 저장소는 음성을 그 문장 목록으로 바꿔 로봇 PC의 추론 명령에 넘기는 작은 서버입니다. 사용자 화면과 팀 화면을 Tailscale로 폰에 엽니다.

```text
사용자 폰  누르고 말하기 ─┐                       ┌─ Groq Whisper: 음성 → "말차 타 줘"
                         ├─ Tailscale HTTPS ─ PC 서버
팀원 폰    버튼        ──┘                       └─ run_robot.py infer --tasks [가루, 물, 젓기]
```

- **로봇 제어**는 [smolvla-bimanual-manipulation](https://github.com/cuk-robotics/smolvla-bimanual-manipulation)이 합니다. 워밍업, feature 검사, 목표 차이 제한이 그 저장소의 `infer`와 같습니다.
- **단계 전환**은 [beverage-autonomy](https://github.com/cuk-robotics/beverage-autonomy)의 end detector가 합니다. 팔이 종료 자세에 2초 머물면 다음 문장으로 넘어갑니다.
- **팀 화면 버튼이 최우선**입니다. 리스트가 도는 중에 누르면 리스트를 버리고 그 동작 하나만 합니다.

> 장치 없는 검사, LeRobot 0.6.1 설정 검사, 실제 음성 인식까지 확인했습니다. 실물 모터 동작과 성공률은 아직 확인하지 않았습니다.

## 빠른 시작: 이 PC에서 음성만 시험

로봇도 Tailscale도 없이 PC 마이크로 음성 주문 흐름을 볼 수 있습니다. Python 3.12 이상이면 설치할 패키지가 없습니다.

1. [Groq 콘솔](https://console.groq.com/keys)에서 무료 키를 만들고, `.env.example`을 `.env`로 복사해 `GROQ_API_KEY`에 넣습니다.
2. 서버를 띄웁니다. `--dry-run`은 로봇을 움직이지 않고 보낼 명령만 출력합니다.
   ```powershell
   python -m beverage_voice phone --dry-run
   ```
3. 로그의 `On this PC, no Tailscale needed` 주소를 Chrome이나 Edge에서 열고 마이크를 허용합니다.
4. 파란 버튼을 누른 채 "말차 타"라고 말하고 손을 뗍니다. 화면에 들은 말과 진행 단계가 나옵니다.
5. 한 잔이 끝나면 "준비 중"이 됩니다. 로그의 팀 화면 주소에서 **주문 받기**를 누르면 다시 말할 수 있습니다.

## 로봇 PC에서 쓰기

| 순서 | 할 일 | 문서 |
| --- | --- | --- |
| 1 | 로봇 저장소와 beverage-autonomy에 패치 두 개 적용, 초기 자세 파일 복사 | [robot_side/README.md](robot_side/README.md) |
| 2 | `.env`에 로봇 저장소 위치, Groq 키, 화면 토큰 두 개 | [docs/setup.md](docs/setup.md#2-설정-파일-env) |
| 3 | 서버 띄우기 | [docs/setup.md](docs/setup.md#3-서버-띄우기) |
| 4 | Tailscale Serve로 폰에 HTTPS로 열기 | [docs/setup.md](docs/setup.md#4-tailscale로-폰에-열기) |
| 5 | 실물 확인 순서와 행사 전 체크리스트 | [robot_side/README.md](robot_side/README.md#4-실물에서-확인하는-순서), [docs/setup.md](docs/setup.md#5-행사-전-확인) |

```powershell
python -m beverage_voice phone `
  --policy-path models/YOUR_PRETRAINED_MODEL `
  --dataset-root data/lerobot/YOUR_DATASET `
  --end-detector C:\path\to\beverage-autonomy\episode_end_detector\config.json
```

서버가 출력하는 화면 주소 두 개는 로봇을 움직이므로 공유하지 않습니다.

## 문서

| 문서 | 내용 |
| --- | --- |
| [docs/setup.md](docs/setup.md) | 로봇 PC 설치, `.env`, 서버 띄우기, Tailscale, 행사 전 확인 |
| [docs/usage.md](docs/usage.md) | 두 화면, 음성 인식, 음료와 task 문장, 옵션, HTTP API, 한계 |
| [docs/protocol.md](docs/protocol.md) | 로봇 저장소와 주고받는 명령, 표준 입력, 표준 출력 형식 |
| [robot_side/README.md](robot_side/README.md) | 로봇 쪽 패치 적용, 초기 자세, 실물 확인 순서, 설계 근거 |
| [AGENTS.md](AGENTS.md) | 코딩 에이전트에게 붙이는 작업을 맡길 때 |

## 폴더 구성

```text
beverage_voice/        서버 코드. 표준 라이브러리만 쓰고 상대 import라 폴더째 옮겨도 된다
  cli.py               진입점과 옵션. 로봇 저장소 cli와 같은 이름
  tasks.py             task 문장, 상한, 음성 키워드
  inference.py         run_robot.py 실행과 프로토콜
  phone.py             HTTP 서버, 토큰, 상태
  stt.py               Whisper 전사 API (Groq 또는 OpenAI)
  web/                 사용자 화면과 팀 화면. 외부 리소스 없는 단일 HTML
docs/                  사용 문서와 프로토콜
robot_side/            로봇 저장소와 beverage-autonomy 패치, 초기 자세 파일
tests/                 장치 없는 검사. fake_robot은 규격대로 말하는 가짜 run_robot.py
.env.example           설정 파일 견본. 복사한 .env는 저장소에 올라가지 않는다
```

## 검사

```powershell
python -m unittest discover -s tests
```

가짜 로봇을 실제 자식 프로세스로 띄워 리스트 실행, 중지, 끼어들기, 초기 위치 이동, 오류 보고를 확인합니다. GitHub에 올리면 `.github/workflows/tests.yml`이 푸시마다 Windows에서 같은 검사를 돌립니다.

## 안전

- 소프트웨어 중지는 비상정지가 아닙니다. 물리 비상정지 담당을 따로 둡니다.
- 복귀와 초기 위치 이동에는 충돌 회피가 없어, 물체를 쥔 채 멈추면 떨어뜨릴 수 있습니다.
- 키와 토큰은 `.env`에만 둡니다. `.env`와 `runs.jsonl`은 `.gitignore`에 있습니다.
