"""한국외대 홈페이지 공지 게시판 수집기.

공개 게시판(일반·학사·장학)의 목록과 본문을 읽어 data/notices.json에 쌓는다.
이미 받아 둔 글은 다시 받지 않되, 최근 글과 마감이 남은 글은 다시 열어
수정·삭제 여부를 확인한다.
"""
import html
import json
import re
import datetime as dt
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://www.hufs.ac.kr"
OIA = "https://oia.hufs.ac.kr"  # 국제교류처 누리집
# 게시판 번호: (이름, 사이트 주소, 사이트 코드)
BOARDS = {
    "2180": ("일반", BASE, "hufs"),
    "2181": ("학사", BASE, "hufs"),
    "2182": ("장학", BASE, "hufs"),
    "2183": ("채용", BASE, "hufs"),
    "3431": ("국제교류", OIA, "oia"),
}
UA = "Mozilla/5.0 (HUFS-NoticeBot; non-profit; contact: innovation support team)"
DELAY = 0.5  # 요청 사이 대기(초)
RECHECK_DAYS = 14  # 이 기간 안에 올라온 글은 수정 여부를 다시 확인
RECHECK_GAP = dt.timedelta(hours=3)  # 같은 글을 다시 확인하는 최소 간격

DATA_DIR = Path(__file__).parent / "data"
DATA_FILE = DATA_DIR / "notices.json"


def fetch(url, post=None):
    data = urllib.parse.urlencode(post).encode() if post else None
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def clean(fragment):
    """HTML 조각을 줄바꿈이 살아 있는 일반 텍스트로 바꾼다."""
    s = re.sub(r"(?is)<(script|style).*?</\1>", "", fragment)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|tr|li|h\d|table)>", "\n", s)
    s = re.sub(r"(?i)</t[dh]>", " ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def parse_list(page_html, board_id, site="hufs"):
    rows = []
    for tr in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", page_html):
        m = re.search(rf"/bbs/{site}/{board_id}/(\d+)/artclView\.do", tr)
        if not m:
            continue
        def td(cls):
            t = re.search(rf'(?is)<td class="{cls}[^"]*">(.*?)</td>', tr)
            return clean(t.group(1)) if t else ""
        rows.append({
            "id": m.group(1),
            "fixed": "notice" in tr[:80] or not td("td-num").isdigit(),
            "title": re.sub(r"\s*새글\s*$", "", td("td-subject")).replace("\n", " "),
            "writer": td("td-write"),
            "date": td("td-date").replace(".", "-"),
        })
    return rows


def parse_view(view_html, base=BASE):
    """본문 영역이 없으면(삭제된 글) None."""
    if '<div class="view-con">' not in view_html:
        return None
    def dd(cls):
        m = re.search(rf'(?is)<dl class="{cls}">.*?<dd>(.*?)</dd>', view_html)
        return clean(m.group(1)) if m else ""
    body = ""
    m = re.search(r'(?is)<div class="view-con">(.*?)<div class="(?:view-navi|board-button)', view_html)
    if m:
        body = clean(m.group(1))
    files = [
        {"name": clean(name), "url": base + href}
        for href, name in re.findall(
            r'(?is)<a href="(/bbs/[^"]+/download\.do)"[^>]*>(.*?)</a>', view_html)
    ]
    return {"body": body, "modified": dd("modify").replace(".", "-"), "files": files}


def load():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {}


def save(store):
    DATA_DIR.mkdir(exist_ok=True)
    DATA_FILE.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")


def _needs_recheck(n, now):
    """최근 글이나 마감이 남은 글이고, 마지막 확인 뒤 충분히 지났으면 다시 확인한다."""
    today = now.date().isoformat()
    recent = n["date"] >= (now.date() - dt.timedelta(days=RECHECK_DAYS)).isoformat()
    deadline = (n.get("x") or {}).get("deadline")
    open_deadline = bool(deadline) and deadline["date"] >= today
    if not (recent or open_deadline):
        return False
    checked = n.get("checked")
    return not checked or now - dt.datetime.fromisoformat(checked) >= RECHECK_GAP


def recheck(store, now, log=print):
    """받아 둔 글을 다시 열어 제목·본문·첨부·수정일이 바뀌었거나 삭제됐는지 확인한다."""
    changed = deleted = 0
    for key, n in store.items():
        if not _needs_recheck(n, now):
            continue
        try:
            view = parse_view(fetch(n["url"]), n["url"].split("/bbs/")[0])
        except Exception as e:
            log(f"  재확인 실패 {n['url']}: {e}")
            continue
        time.sleep(DELAY)
        n["checked"] = now.isoformat(timespec="seconds")
        if view is None:
            if not n.get("deleted"):
                n["deleted"] = True
                deleted += 1
                log(f"  삭제됨: {n['title']}")
            continue
        n.pop("deleted", None)
        if any(n.get(k) != view[k] for k in ("body", "files")):  # 수정일만 바뀐 건 표시하지 않음
            n["changed"] = now.isoformat(timespec="seconds")
            changed += 1
            log(f"  수정됨: {n['title']}")
        n.update(view)
    log(f"재확인: 수정 {changed}건, 삭제 {deleted}건")


def collect(pages=2, log=print):
    store = load()
    now = dt.datetime.now()
    new = 0
    for bid, (bname, base, site) in BOARDS.items():
        list_url = f"{base}/bbs/{site}/{bid}/artclList.do"
        for page in range(1, pages + 1):
            rows = parse_list(fetch(list_url, {"page": page}), bid, site)
            time.sleep(DELAY)
            for row in rows:
                key = f"{bid}-{row['id']}"
                if key in store:
                    old = store[key]
                    old["fixed"] = old["fixed"] or row["fixed"]
                    if old["title"] != row["title"]:  # 목록에서 제목이 바뀐 건 바로 반영
                        old["title"] = row["title"]
                        old.pop("checked", None)  # 본문도 이번에 다시 확인
                    continue
                view_url = f"{base}/bbs/{site}/{bid}/{row['id']}/artclView.do"
                try:
                    view = parse_view(fetch(view_url), base)
                except Exception as e:  # 한 글이 실패해도 나머지는 계속
                    log(f"  본문 실패 {view_url}: {e}")
                    continue
                time.sleep(DELAY)
                if view is None:
                    continue
                store[key] = {**row, "board": bname, "url": view_url, **view,
                              "checked": now.isoformat(timespec="seconds")}
                new += 1
        log(f"[{bname}] 수집 완료 (누적 {sum(1 for v in store.values() if v['board'] == bname)}건)")
    recheck(store, now, log)
    save(store)
    log(f"새 공지 {new}건, 전체 {len(store)}건")
    return store


if __name__ == "__main__":
    collect()
