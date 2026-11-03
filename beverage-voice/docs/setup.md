# 로봇 PC에 설치하기

로봇을 실제로 움직이는 PC에서 한 번 하는 준비입니다. 순서대로 하면 폰으로 음성 주문을 받을 수 있습니다.

1. [로봇 저장소 준비](#1-로봇-저장소-준비)
2. [설정 파일 `.env`](#2-설정-파일-env)
3. [서버 띄우기](#3-서버-띄우기)
4. [Tailscale로 폰에 열기](#4-tailscale로-폰에-열기)
5. [행사 전 확인](#5-행사-전-확인)

## 1. 로봇 저장소 준비

로봇 제어는 [smolvla-bimanual-manipulation](https://github.com/cuk-robotics/smolvla-bimanual-manipulation)의 `scripts/run_robot.py`가 합니다. 그 저장소 안내대로 `configs/local/hardware.json`과 보정 파일을 준비하고, `infer --validate-only`가 통과하는 상태로 만듭니다.

그다음 이 폴더의 패치 두 개를 붙입니다. 한 세션에서 가루, 물, 젓기를 이어 도는 `infer --tasks`와, 팔을 초기 자세로 보내는 `home` 모드가 들어갑니다. 방법은 [robot_side/README.md](../robot_side/README.md)에 있습니다.

## 2. 설정 파일 `.env`

이 폴더의 `.env.example`을 `.env`로 복사해 값을 채웁니다. 서버가 시작할 때 읽습니다.

```powershell
copy .env.example .env
notepad .env
```

```text
CUK_ROBOTICS_ROOT=C:\path\to\smolvla-bimanual-manipulation
GROQ_API_KEY=gsk_...
BEVERAGE_DEMO_TOKEN=q3Vx...
BEVERAGE_TEAM_TOKEN=Zk8p...
```

| 이름 | 내용 |
| --- | --- |
| `CUK_ROBOTICS_ROOT` | 로봇 저장소 위치 |
| `GROQ_API_KEY` | 음성 인식 키. [Groq 콘솔](https://console.groq.com/keys)에서 무료로 만듭니다 |
| `BEVERAGE_DEMO_TOKEN` | 사용자 화면 주소에 붙는 토큰 |
| `BEVERAGE_TEAM_TOKEN` | 팀 화면 주소에 붙는 토큰. 사용자 화면 토큰과 달라야 합니다 |
| `CUK_ROBOTICS_PYTHON` | 로봇 저장소가 다른 Python을 쓸 때만. 그 python.exe 경로 |
| `OPENAI_API_KEY` | Groq 대신 OpenAI로 인식할 때만. 둘 다 있으면 Groq을 씁니다 |

토큰은 주소에 들어가니 영문과 숫자로 만듭니다. 아래 명령을 두 번 돌려 나온 값을 하나씩 넣습니다.

```powershell
python -c "import secrets; print(secrets.token_urlsafe(16))"
```

- 토큰을 비워 두면 서버를 띄울 때마다 새로 만들어져서 폰에 열어 둔 화면이 끊깁니다.
- 셸에서 같은 이름의 환경 변수를 정했으면 그쪽이 `.env`보다 먼저입니다.
- `.env`는 `.gitignore`에 있어 저장소에 올라가지 않습니다. 키와 토큰이 들어 있으니 파일째 공유하지 않습니다.

## 3. 서버 띄우기

```powershell
python -m beverage_voice phone `
  --policy-path models/YOUR_PRETRAINED_MODEL `
  --dataset-root data/lerobot/YOUR_DATASET `
  --end-detector C:\path\to\beverage-autonomy\episode_end_detector\config.json
```

두 경로는 로봇 저장소 기준이고, 데이터셋은 파인튜닝에 쓴 것입니다. 띄우면 아래 순서로 진행됩니다.

1. infer를 `--validate-only`로 한 번 돌립니다. 모터와 가중치는 건드리지 않고, 옵션 오타나 경로 오류, 패치가 안 붙은 로봇 저장소를 첫 주문 전에 잡습니다. 실패하면 이유를 보여 주고 서버를 띄우지 않습니다.
2. 사용자 화면과 팀 화면 주소를 토큰과 함께 출력합니다. **두 주소 모두 로봇을 움직이므로 공유하지 않습니다.**
3. 음성 인식 서비스와 Tailscale Serve 상태를 알려 줍니다.

```text
infer check passed.
Tailscale serve: on
Demo page (keep private): https://<PC 이름>.<테일넷>.ts.net/?token=...
Team page (keep private): https://<PC 이름>.<테일넷>.ts.net/team?token=...
On this PC, no Tailscale needed (microphone works): http://127.0.0.1:8000/?token=...
On this PC, team page: http://127.0.0.1:8000/team?token=...
Speech: groq whisper-large-v3
```

로봇 PC 앞에서 시험할 때는 `On this PC` 주소를 그 PC 브라우저로 열면 됩니다. 브라우저는 `127.0.0.1`에서는 HTTPS가 아니어도 마이크를 허락합니다.

## 4. Tailscale로 폰에 열기

서버는 PC의 localhost에만 열립니다. `tailscale serve`가 본인 테일넷 안에서만 HTTPS로 중계하므로 같은 Wi-Fi의 다른 기기나 인터넷에서는 들어올 수 없습니다. 폰 브라우저는 HTTPS가 아니면 마이크를 막기 때문에 이 단계가 필요합니다.

처음 한 번만 합니다.

1. PC와 폰에 [Tailscale](https://tailscale.com/download)을 설치하고 같은 계정으로 로그인합니다.
2. 관리 콘솔 [DNS 메뉴](https://login.tailscale.com/admin/dns)에서 MagicDNS와 **HTTPS Certificates**를 켭니다. 인증서를 켜면 기기 이름이 공개 인증서 기록에 남습니다.
3. PC에서 아래를 실행합니다. 테일넷에서 Serve가 꺼져 있으면 승인 링크가 나옵니다. 기기마다 다른 링크이니 출력된 것을 열어 승인하고 같은 명령을 다시 실행합니다. 설정은 재부팅 뒤에도 남습니다.

```powershell
tailscale serve --bg 8000
tailscale serve status    # https://<PC 이름>.<테일넷>.ts.net 이 보이면 켜진 것
```

폰에서는 서버 시작 로그의 `Demo page`, `Team page` 주소를 엽니다. PC 이름은 관리 콘솔 [기기 목록](https://login.tailscale.com/admin/machines)에서도 볼 수 있습니다.

- Serve를 끄려면 `tailscale serve --https=443 off`를 실행합니다.
- Serve를 못 켰으면 `--host (tailscale ip -4)`로 테일스케일 IP에 직접 열 수 있습니다. HTTP라 마이크는 안 되고 팀 화면 버튼만 씁니다.
- 공식 설명: [Serve](https://tailscale.com/docs/features/tailscale-serve), [HTTPS 인증서](https://tailscale.com/docs/how-to/set-up-https-certificates)

## 5. 행사 전 확인

- [ ] [robot_side/README.md](../robot_side/README.md) 4절의 실물 확인을 마쳤다.
- [ ] 로봇 저장소에서 `run_robot.py preview --wrist-fourcc MJPG`로 카메라 번호와 OBS 가상 카메라를 확인했고, 보정 파일 4개가 있다.
- [ ] 서버 시작 로그에 `infer check passed.`와 `Speech: groq whisper-large-v3`가 나온다.
- [ ] `tailscale serve status`에 포트가 보이고, 행사장 네트워크에서 폰으로 두 주소를 실제로 열어 봤다.
- [ ] 행사장 네트워크에서 폰으로 "말차 타"를 다섯 번쯤 말해 들은 말이 맞게 나오고, 응답이 몇 초 안에 오는지 봤다. 느리면 콘솔의 `Transcription retry` 줄로 연결 문제인지 확인한다.
- [ ] 중지 담당자와 물리 비상정지 위치를 정했다.
