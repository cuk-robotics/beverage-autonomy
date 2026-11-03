"""음료별 task 문장, 상한 시간, 음성 키워드.

infer에 넘기는 값은 항상 task 문장 그 자체다(로봇 저장소 cli의 --task와 같다). 짧은 이름은 사람이
고르기 편하라고 둔 별칭이고 resolve_task()에서 바로 문장으로 바뀐다.
"""
import math
import re
import unicodedata

# 학습 데이터에 저장된 task 문장과 글자 하나까지 같아야 한다. 정책은 이 문장을 보고 무슨 동작을 할지 고른다.
# 원문은 로봇 저장소 docs/recording.md. 음료 키는 데이터셋 ID so101_powder_<음료>_... 에서 따왔다.
# 2026-11-03에 docs/datasets.md의 공개 통합 데이터셋 5개 meta/tasks.parquet와 대조해 다섯 문장이 모두 같았다.
# 다른 데이터셋으로 파인튜닝했다면 로봇 쪽 infer가 --dataset-root의 tasks.parquet에 없는 문장을 시작할 때 경고한다.
POWDER_TASKS = {
    "coffee": "Pour coffee powder from the red-lidded container into the cup.",
    "icetea": "Pour iced tea powder from the blue-lidded container into the cup.",
    "greentea": "Pour green tea latte powder from the green-lidded container into the cup.",
}
WATER_TASK = "Pour water into the cup."
STIR_TASK = "Stir the mixture in the cup with the muddler."

# 수동 실행에서 쓰는 단계 이름. 데이터셋 ID의 so101_<단계>_<음료> 순서를 따른다.
# 물과 젓기는 음료와 상관없이 문장이 하나라 음료를 붙이지 않았다.
TASK_NAMES = {
    "powder_coffee": POWDER_TASKS["coffee"],
    "powder_icetea": POWDER_TASKS["icetea"],
    "powder_greentea": POWDER_TASKS["greentea"],
    "water": WATER_TASK,
    "stir": STIR_TASK,
}

# task별 상한 초. end detector가 울리지 않을 때의 안전장치라 수집 평균의 두 배쯤 잡았다.
# 수집 평균은 데이터셋 프레임 수로 계산하면 가루 약 46초, 물 37초, 젓기 45초다. 수집 문서 표의 20/25/30초는
# 재배치 시간이지 회차 길이가 아니다. 실물에서 잰 값으로 다시 맞춘다.
DEFAULT_DURATION_S = {
    **{task: 100.0 for task in POWDER_TASKS.values()},
    WATER_TASK: 75.0,
    STIR_TASK: 90.0,
}
MAX_MANUAL_DURATION_S = 300.0  # 수동 실행 시간 상한

# 소문자로 바꾸고 공백과 문장부호를 지운 뒤 포함 여부로 본다. "아이스 티", "Ice tea."도 잡힌다.
# 전사가 영어로 적는 경우와 자주 틀리는 표기를 같이 넣었다. "카페"는 일상어라 잡담에 반응해서 뺐다.
KEYWORDS = {
    "말차": "greentea", "맛차": "greentea", "말챠": "greentea", "마차": "greentea", "녹차": "greentea",
    "그린티": "greentea", "matcha": "greentea", "macha": "greentea", "greentea": "greentea",
    "아이스티": "icetea", "아이스tea": "icetea", "아이스t": "icetea", "아이스띠": "icetea", "아이스디": "icetea",
    "icetea": "icetea", "icedtea": "icetea",
    "커피": "coffee", "코피": "coffee", "꺼피": "coffee", "커삐": "coffee", "아메리카노": "coffee",
    "coffee": "coffee", "coffe": "coffee", "cofee": "coffee",
}
# 위 표기로 못 찾았을 때만 쓰는 유사 판정 대상. 글자 수가 같은 구간을 자모로 풀어 한 자모 차이까지 받는다.
# 첫 자음은 같아야 한다. "알차게"가 말차로, "발차"가 말차로 잡히지 않게 하려는 것이다.
FUZZY_KEYWORDS = {"말차": "greentea", "녹차": "greentea", "그린티": "greentea", "아이스티": "icetea", "커피": "coffee"}
FUZZY_MAX_EDITS = 1
FUZZY_IGNORE = ("녹초",)  # 한 자모 차이지만 실제 낱말이라 유사 판정에서 뺀다
# 전사 API에 미리 알려 주는 낱말. 모델이 처음부터 이 표기로 적을 가능성이 높아진다.
STT_PROMPT = "음료 주문입니다. 말차, 아이스티, 커피 중 하나를 말합니다. 예: 말차 타 줘, 아이스티 주세요, 커피 한 잔."


def _flat(text):
    return re.sub(r"[\W_]+", "", text.lower())


def _jamo(syllables):
    return unicodedata.normalize("NFD", syllables)  # 한글 음절을 초성·중성·종성으로 푼다


def _edits(a, b):
    """편집 거리. 짧은 문자열만 비교하니 단순한 표로 충분하다."""
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, y in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (x != y))
    return row[-1]


def _sounds_like(flat, keyword):
    target = _jamo(keyword)
    for start in range(len(flat) - len(keyword) + 1):
        window = _jamo(flat[start:start + len(keyword)])
        if window[0] == target[0] and _edits(window, target) <= FUZZY_MAX_EDITS:
            return True
    return False


def drinks_in(text):
    """문장에 나온 음료 키 집합. 정확한 표기가 없을 때만 발음이 비슷한 말을 찾는다."""
    flat = _flat(text)
    exact = {drink for keyword, drink in KEYWORDS.items() if keyword in flat}
    if exact:
        return exact
    for word in FUZZY_IGNORE:
        flat = flat.replace(word, "")
    return {drink for keyword, drink in FUZZY_KEYWORDS.items() if _sounds_like(flat, keyword)}


def match_drink(text):
    """문장에 나온 음료 하나를 돌려준다. 음료가 없거나 두 개 이상이면 None.

    "커피 말고 말차"처럼 둘이 같이 들리면 뭘 원하는지 확실하지 않으니 아무것도 하지 않는다.
    """
    drinks = drinks_in(text)
    return drinks.pop() if len(drinks) == 1 else None


def drink_tasks(drink):
    """음료 하나를 만드는 task 문장 목록. 가루, 물, 젓기 순서.

    beverage-autonomy의 EpisodeEndDetector.start_episode(instructions)에 그대로 넘길 수 있는 형태다.
    """
    tasks = [POWDER_TASKS[drink], WATER_TASK]
    if drink != "icetea":  # 젓기 학습 데이터는 커피와 말차뿐이다. 아이스티 젓기를 수집하면 이 조건을 지운다
        tasks.append(STIR_TASK)
    return tasks


def resolve_task(value):
    """단계 이름이나 task 문장을 받아 학습된 task 문장으로 돌려준다. 둘 다 아니면 ValueError.

    학습한 문장만 받는다. 아무 문장이나 넘기면 정책이 본 적 없는 동작을 할 위험이 있다.
    """
    task = TASK_NAMES.get(value, value)
    if task not in DEFAULT_DURATION_S:
        raise ValueError(f"unknown task {value!r}; choose one of {', '.join(TASK_NAMES)}")
    return task


def check_manual_duration(duration_s):
    """수동 실행 시간을 float로 돌려준다. 1~300초 밖이거나 숫자가 아니면 ValueError."""
    duration_s = float(duration_s)
    if not (math.isfinite(duration_s) and 1 <= duration_s <= MAX_MANUAL_DURATION_S):
        raise ValueError(f"duration_s must be 1-{MAX_MANUAL_DURATION_S:g} s")
    return duration_s


def plan(tasks, duration_s=None):
    """task 문장 목록을 (문장, 상한 초) 목록으로. duration_s를 주면 모든 문장에 그 값을 쓴다."""
    return [(task, float(duration_s) if duration_s else DEFAULT_DURATION_S[task]) for task in tasks]
