"""공지 본문에서 캠퍼스·대상·분류·마감일을 뽑고 세 줄 요약을 만든다.

기본은 규칙 기반이라 API 키 없이 돈다. ANTHROPIC_API_KEY가 있고 --ai를 주면
Claude가 같은 항목을 뽑고, 결과는 공지마다 한 번만 저장해 다시 쓴다.
"""
import datetime as dt
import hashlib
import json
import os
import re

ROLES = ["학부생", "대학원생", "교원", "직원"]
CATEGORIES = ["장학", "학사", "채용", "취업·진로", "국제교류", "비교과(행사·특강 등)", "기타"]

CATEGORY_WORDS = {
    "장학": ["장학", "학자금", "근로장학"],
    "학사": ["수강신청", "수강정정", "수강확인", "학점", "졸업", "전공", "휴학", "복학", "시험", "계절학기", "성적", "이수", "학적", "폐강"],
    "취업·진로": ["취업", "채용", "인턴", "현장실습", "진로", "커리어", "창업"],
    "국제교류": ["교환학생", "해외", "파견", "어학연수", "국제교류", "유학생"],
    "비교과(행사·특강 등)": ["특강", "축제", "세미나", "행사", "대회", "공모전", "포럼", "캠프", "설명회", "프로그램",
                         "멘토", "봉사", "서포터즈", "수강생", "교육생", "참가자", "워크숍", "클리닉", "북토크",
                         "체험", "동아리", "캠페인"],
    "기타": ["예비군", "법정의무교육", "보안", "안전", "냉방", "난방", "주차", "기숙사", "예방",
             "입학", "신입생", "입사생", "포상"],
}
ROLE_WORDS = {
    "학부생": ["학부생", "재학생", "신입생", "졸업예정자", "휴학생", "학부"],
    "대학원생": ["대학원생", "석사", "박사", "대학원"],
    "교원": ["교원", "교수", "강사", "전임"],
    "직원": ["직원", "교직원"],
}
DEADLINE_STRONG = ["마감", "기한", "까지", "제출기간", "신청기간", "접수기간", "지원기간", "모집기간"]
DEADLINE_WEAK = ["기간", "일정", "신청", "접수", "제출", "~"]

# 2026. 10. 29 / 2026-10-29 / 2026년 10월 29일 / 10. 29 / 10월 29일 / 10/29
DATE_RE = re.compile(
    r"(?:(?P<y>20\d{2})\s*(?:[.\-/]|년)\s*)?"
    r"(?P<m>1[0-2]|0?[1-9])\s*(?:[.\-/]|월)\s*"
    r"(?P<d>3[01]|[12]\d|0?[1-9])(?!\d)"
    r"(?!\s*(?:학기|학년|학점|차|명|원|만|%|개|건|시간|회|기|위|유형))\s*(?:일|\.)?"
)
TIME_RE = re.compile(r"(?P<ampm>오전|오후)?\s*(?<!\d)(?P<h>[01]?\d|2[0-4])(?:\s*:\s*(?P<mi>[0-5]\d)|\s*시)")


def _time(fragment):
    m = TIME_RE.search(fragment)
    if not m:
        return ""
    h = int(m.group("h"))
    if m.group("ampm") == "오후" and h < 12:
        h += 12
    return f"{h:02d}:{m.group('mi') or '00'}"
TITLE_DUE_RE = re.compile(r"\(\s*~\s*\d")  # 제목의 "(~11/23)" 표기


def _campus(n):
    head = n["title"] + " " + n["writer"]
    seoul = bool(re.search(r"서울", head))
    glob = bool(re.search(r"글로벌", head))
    if seoul and not glob:
        return "서울"
    if glob and not seoul:
        return "글로벌"
    return "공통"


def _hits(text, table):
    return [k for k, words in table.items() if any(w in text for w in words)]


def _grades(text):
    found = set()
    for a, b in re.findall(r"([1-6])\s*[~∼\-]\s*([1-6])\s*학년", text):
        found.update(range(int(a), int(b) + 1))
    found.update(int(g) for g in re.findall(r"(?<![~∼\-\d])([1-6])\s*학년", text))
    return sorted(found)


def _dates_in(line, posted):
    out = []
    for m in DATE_RE.finditer(line):
        try:
            y = int(m.group("y")) if m.group("y") else posted.year
            d = dt.date(y, int(m.group("m")), int(m.group("d")))
        except ValueError:
            continue
        if not m.group("y") and d < posted - dt.timedelta(days=60):
            d = d.replace(year=d.year + 1)  # 연말 공지의 1~2월 마감
        out.append((d, m.end()))
    return out


def find_deadline(n):
    """마감 후보 중 가장 그럴듯한 날짜와 근거 문장을 고른다."""
    posted = dt.date.fromisoformat(n["date"])
    best = None  # (score, date, time, evidence)
    for i, line in enumerate([n["title"]] + n["body"].splitlines()):
        dates = _dates_in(line, posted)
        if not dates:
            continue
        weak = [w for w in DEADLINE_WEAK if w in line]
        strong = (any(w in line for w in DEADLINE_STRONG)
                  or (i == 0 and TITLE_DUE_RE.search(line))
                  or ("~" in weak and len(weak) > 1))  # "신청기간 A~B" 같은 범위
        if not (strong or weak):
            continue
        end, end_pos = max(dates)  # 범위면 끝 날짜
        if end < posted or end > posted + dt.timedelta(days=200):
            continue
        t = _time(line[end_pos:end_pos + 15])
        # 강한 단서 > 가까운 날짜 > 시간이 적힌 본문 문장
        score = (2 if strong else 1, -abs((end - posted).days) / 1000, 1 if t else 0)
        if best is None or score > best[0]:
            best = (score, end, t, line[:160])
    if not best:
        return None
    return {
        "date": best[1].isoformat(),
        "time": best[2],
        "evidence": best[3],
        "confidence": "high" if best[0][0] == 2 else "low",
    }


def _clean_title(title):
    return re.sub(r"^(\s*[\[(][^\])]{1,12}[\])]\s*)+", "", title).strip()


ACTIONS = [  # (단서, 할 일) — 위에서부터 먼저 맞는 것
    (["출근부"], "출근부 제출"),
    (["지원서", "지원기간", "지원 기간", "장학생 모집", "장학생 선발", "선발"], "신청서(지원서) 제출"),
    (["서류", "제출"], "서류 제출"),
    (["이수", "수강기간", "의무교육"], "기간 안에 이수 완료"),
    (["다운로드", "발급"], "기한 안에 발급·다운로드"),
    (["수강신청", "수강 신청", "계절학기"], "수강 신청"),
    (["신청", "접수", "모집"], "신청(접수)"),
    (["일시", "특강", "행사", "축제", "설명회"], "행사 참석(일정 확인)"),
    (["종료", "변경", "중단"], "변경 사항 확인"),
]


def _action(n, deadline):
    text = (deadline["evidence"] + " " if deadline else "") + n["title"]
    for cues, action in ACTIONS:
        if any(c in text for c in cues):
            return action
    return "내용 확인"


def _gist(n):
    for line in n["body"].splitlines():
        if len(line) >= 20 and not re.match(r"^[-*※\d가-하]\s*[.)]?\s*$", line):
            return line[:110] + ("…" if len(line) > 110 else "")
    return ""


def _final_cats(n, cats):
    """장학·학사·채용·국제교류 게시판 글은 그 분류 하나만, 나머지는 키워드 분류(없으면 기타)."""
    if n["board"] == "국제교류" and ADMIN_RE.search(n["title"]):
        return ["기타"]
    if n["board"] in ("장학", "학사", "채용", "국제교류"):
        return [n["board"]]
    if "기타" in cats and "비교과(행사·특강 등)" in cats:  # 예방 캠페인처럼 둘 다 걸리면 기타
        cats = [c for c in cats if c != "비교과(행사·특강 등)"]
    return [c for c in CATEGORIES if c in cats] or ["기타"]


# 국제교류처 게시판에 올라오는 행정 공지(근무·휴무 등)는 국제교류가 아니라 기타로
ADMIN_RE = re.compile(r"단축근무|근무시간|휴무|휴관|휴가|정전|사무실 이전|업무 중단|시스템 점검")
PROFESSOR_RE = re.compile(r"교수|교원|강사|초빙|faculty", re.I)


def _final_roles(n, roles):
    """채용 공고는 신분과 무관하게 보이도록 비우고, 교수·강사 초빙만 대학원생·교원에게 보인다."""
    if n["board"] != "채용":
        return roles
    return ["대학원생", "교원"] if PROFESSOR_RE.search(n["title"]) else []


def rule_extract(n):
    text = n["title"] + "\n" + n["body"]
    cats = _hits(n["title"], CATEGORY_WORDS)
    if not cats:  # 제목에 단서가 없으면 본문에서 가장 많이 나온 분류 하나만
        counts = {k: sum(n["body"].count(w) for w in ws) for k, ws in CATEGORY_WORDS.items()}
        top = max(counts, key=counts.get)
        cats = [top] if counts[top] else []
    roles = _hits(n["title"], ROLE_WORDS)
    if not roles and n["board"] in ("학사", "장학", "국제교류"):
        roles = ["학부생"] + (["대학원생"] if "대학원" in n["body"] else [])
    elif not roles:
        roles = _hits(n["body"], ROLE_WORDS)
    grades = _grades(text)
    who = "·".join(roles) if roles else "전체 구성원"
    roles = _final_roles(n, roles)
    if grades:
        who += f" ({grades[0]}~{grades[-1]}학년)" if len(grades) > 1 else f" ({grades[0]}학년)"
    campus = _campus(n)
    deadline = find_deadline(n)
    return {
        "campus": campus,
        "roles": roles,
        "grades": grades,
        "categories": _final_cats(n, cats),
        "deadline": deadline,
        "who": (f"{campus}캠퍼스 " if campus != "공통" else "") + who,
        "what": _action(n, deadline),
        "topic": _clean_title(n["title"]),
        "gist": _gist(n),
        "by": "rule",
    }


# ---------- Claude 추출(선택) ----------

AI_SCHEMA = {
    "type": "object",
    "properties": {
        "campus": {"type": "string", "enum": ["서울", "글로벌", "공통"]},
        "roles": {"type": "array", "items": {"type": "string", "enum": ROLES}},
        "grades": {"type": "array", "items": {"type": "integer"}},
        "categories": {"type": "array", "items": {"type": "string", "enum": CATEGORIES}},
        "deadline_date": {"type": "string", "description": "YYYY-MM-DD, 없으면 빈 문자열"},
        "deadline_time": {"type": "string", "description": "HH:MM, 없으면 빈 문자열"},
        "deadline_evidence": {"type": "string", "description": "마감일 근거가 된 원문 문장 그대로"},
        "confidence": {"type": "string", "enum": ["high", "low"]},
        "who": {"type": "string", "description": "누구에게 해당하는지 30자 이내"},
        "what": {"type": "string", "description": "무엇을 해야 하는지 40자 이내"},
        "gist": {"type": "string", "description": "핵심 한 문장 80자 이내"},
    },
    "required": ["campus", "roles", "grades", "categories", "deadline_date", "deadline_time",
                 "deadline_evidence", "confidence", "who", "what", "gist"],
    "additionalProperties": False,
}

AI_PROMPT = """한국외국어대학교 홈페이지 공지 한 건이다. 학생이 자기에게 해당하는지, 언제까지 무엇을 해야 하는지 바로 알 수 있게 항목을 뽑아라.
- 마감일은 신청·접수·제출이 끝나는 날이다. 행사 당일만 있으면 그 날을 쓴다. 날짜가 없으면 빈 문자열.
- 연도가 없는 날짜는 게시일({posted}) 기준으로 가장 가까운 미래로 본다.
- 근거 문장은 원문에서 그대로 옮긴다. 확실하지 않으면 confidence를 low로.
- 원문에 없는 내용은 만들지 마라.

게시판: {board} / 게시일: {posted} / 작성부서: {writer}
제목: {title}

본문:
{body}"""


def ai_extract(n, client, model):
    response = client.beta.messages.create(
        model=model,
        max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": AI_SCHEMA}},
        messages=[{"role": "user", "content": AI_PROMPT.format(
            posted=n["date"], board=n["board"], writer=n["writer"],
            title=n["title"], body=n["body"] or "(본문이 이미지라 텍스트 없음)")}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("모델이 응답을 거절함")
    r = json.loads(next(b.text for b in response.content if b.type == "text"))
    deadline = None
    if r["deadline_date"]:
        deadline = {"date": r["deadline_date"], "time": r["deadline_time"],
                    "evidence": r["deadline_evidence"], "confidence": r["confidence"]}
    return {
        "campus": r["campus"], "roles": _final_roles(n, r["roles"]), "grades": r["grades"],
        "categories": _final_cats(n, r["categories"]), "deadline": deadline,
        "who": r["who"], "what": r["what"], "topic": _clean_title(n["title"]),
        "gist": r["gist"], "by": model,
    }


RULE_VERSION = "10"  # 규칙을 고치면 올려서 저장된 결과를 다시 만든다


def _hash(n):
    return hashlib.sha1((RULE_VERSION + n["title"] + n["body"]).encode("utf-8")).hexdigest()[:12]


def enrich(store, use_ai=False, log=print):
    client = model = None
    if use_ai:
        try:
            import anthropic
            client = anthropic.Anthropic()
            model = os.environ.get("NOTICE_MODEL", "claude-opus-5-5")
        except ImportError:
            log("anthropic 패키지가 없어 규칙 기반으로만 처리합니다 (pip install anthropic).")
    done = 0
    for n in store.values():
        h = _hash(n)
        prev = n.get("x")
        if prev and prev.get("hash") == h and (prev["by"] != "rule" or not client):
            continue
        x = None
        if client:
            try:
                x = ai_extract(n, client, model)
            except Exception as e:  # API 오류면 규칙 기반으로 대신한다
                log(f"  AI 추출 실패({n['title'][:20]}…): {e}")
        n["x"] = {**(x or rule_extract(n)), "hash": h}
        done += 1
    log(f"추출 {done}건 갱신 ({'Claude' if client else '규칙 기반'})")
    return store
