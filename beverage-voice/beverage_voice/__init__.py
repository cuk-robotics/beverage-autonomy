"""SO-101 양팔 음료 로봇을 폰으로 움직이는 패키지.

cli         진입점과 옵션. 로봇 저장소 teleoperation/cli.py와 같은 짜임
tasks       task 문장, 상한 시간, 음성 키워드
inference   run_robot.py infer 호출. Session이 프로세스를 띄우고 BEVERAGE 이벤트를 올린다
stt         Whisper 전사 API 호출 (Groq 또는 OpenAI)
phone       폰 페이지 서버. 시연 화면(/)과 팀 화면(/team)

로봇 제어 코드는 여기 없고 smolvla-bimanual-manipulation의 run_robot.py infer를 불러서 쓴다. 두 쪽이
주고받는 형식은 docs/protocol.md, 로봇 저장소에 적용할 패치는 robot_side/에 있다. 패키지 안에서는 상대 import만 써서 폴더째 옮겨도 import를 고칠
필요가 없다. import만 해서는 장치에 아무 일도 생기지 않는다.
"""
