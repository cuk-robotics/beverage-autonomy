"""전사 API 호출. 폰이 보낸 녹음을 PC가 받아 적는다.

Groq과 OpenAI 둘 다 같은 형식의 Whisper 전사 API를 연다. 모델을 이 PC에 내려받지 않는다. 키는 환경 변수로만
받고 폰이나 저장소로 넘기지 않는다.
"""
import json
import os
import secrets
import socket
import urllib.error
import urllib.request

# 전사 서비스. GROQ_API_KEY가 있으면 Groq, 없으면 OpenAI를 쓴다(--stt-provider로 고를 수도 있다).
# Groq의 whisper-large-v3는 무료 등급이 있고, 한국어는 turbo보다 이쪽이 더 정확하다.
PROVIDERS = {
    "groq": {"url": "https://api.groq.com/openai/v1/audio/transcriptions", "key_env": "GROQ_API_KEY",
             "model": "whisper-large-v3"},
    "openai": {"url": "https://api.openai.com/v1/audio/transcriptions", "key_env": "OPENAI_API_KEY",
               "model": "whisper-1"},
}
# 네트워크에 따라 TCP 연결이 가끔 수십 초 매달린다. 시험한 PC에서는 연결 넷 중 둘이 21초 만에 실패했고,
# 그 직후 새 연결은 1~3초에 붙었다. 오래 기다리지 말고 새 연결로 다시 해 본다. 처리 자체는 0.5초 안팎이다.
# 주소가 둘이라 한 번 시도에 연결 대기가 최대 6초 × 2다. 두 번 시도하면 24초로, 사용자 화면이 포기하는
# 30초보다 짧다. 더 늘리면 화면은 실패로 보이는데 뒤늦게 주문이 들어가 로봇이 움직일 수 있다.
TIMEOUT_S = 6
ATTEMPTS = 2
# 브라우저마다 녹음 형식이 다르다. 안드로이드 크롬은 webm, iOS 사파리는 mp4로 보낸다.
# API가 파일 확장자로 형식을 알아보니 Content-Type에 맞춰 이름을 붙여 준다.
EXTENSIONS = {"audio/webm": "webm", "audio/mp4": "mp4", "audio/ogg": "ogg", "audio/wav": "wav", "audio/mpeg": "mp3"}


class TranscribeError(Exception):
    """전사 실패. code는 화면 안내용 짧은 분류, 메시지는 원인 원문."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def default_provider():
    return "groq" if os.environ.get(PROVIDERS["groq"]["key_env"]) else "openai"


def transcribe(audio, content_type, model, api_key, prompt="", provider="openai"):
    """녹음 하나를 전사 API에 보내 한국어 텍스트를 받아 온다. 실패하면 TranscribeError.

    multipart 본문은 직접 만든다. requests 같은 패키지를 깔지 않으려고 이렇게 했다.
    prompt는 모델에게 미리 알려 주는 낱말이다. 음료 이름을 주면 비슷한 소리를 그 표기로 적는 쪽으로 기운다.
    """
    boundary = secrets.token_hex(12)
    extension = EXTENSIONS.get(content_type.split(";")[0].strip(), "webm")
    body = b"".join([
        f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{model}\r\n'.encode(),
        f'--{boundary}\r\nContent-Disposition: form-data; name="language"\r\n\r\nko\r\n'.encode(),
        f'--{boundary}\r\nContent-Disposition: form-data; name="prompt"\r\n\r\n{prompt}\r\n'.encode() if prompt else b"",
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="speech.{extension}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode(),
        audio, f"\r\n--{boundary}--\r\n".encode(),
    ])
    request = urllib.request.Request(PROVIDERS[provider]["url"], data=body, headers={
        "Authorization": f"Bearer {api_key}", "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "beverage-voice/1.0"})  # Groq 앞단(Cloudflare)은 urllib 기본 User-Agent를 403 1010으로 막는다
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return _send(request)
        except TranscribeError as error:
            if error.code not in ("timeout", "no_internet") or attempt == ATTEMPTS:
                raise
            print(f"Transcription retry after {error.code}: {error}", flush=True)


def _send(request):
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return json.load(response)["text"].strip()
    except urllib.error.HTTPError as error:  # 서버가 답은 했다. 상태 코드로 원인을 가른다
        detail = error.read().decode("utf-8", "replace")[:300]
        error.close()
        if error.code in (401, 403):
            raise TranscribeError("api_key", f"API key rejected ({error.code}): {detail}") from None
        if error.code == 429:
            raise TranscribeError("api_limit", f"API rate limit ({error.code}): {detail}") from None
        if error.code == 400:
            raise TranscribeError("bad_audio", f"API rejected the recording ({error.code}): {detail}") from None
        raise TranscribeError("api_down", f"API error {error.code}: {detail}") from None
    except (socket.timeout, TimeoutError):
        raise TranscribeError("timeout", f"no answer from the API within {TIMEOUT_S}s") from None
    except urllib.error.URLError as error:  # DNS 실패, 연결 거부 등. PC 인터넷 쪽 문제
        raise TranscribeError("no_internet", f"cannot reach the API: {error.reason}") from None
    except (OSError, KeyError, ValueError) as error:
        raise TranscribeError("api_down", f"unexpected API response: {error}") from None
