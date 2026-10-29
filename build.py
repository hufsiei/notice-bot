"""수집·추출한 공지로 정적 웹페이지(site/index.html)와 캘린더(.ics)를 만든다."""
import datetime as dt
import difflib
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent
SITE = ROOT / "site"
TEMPLATE = ROOT / "template.html"


def _ics_escape(s):
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line):
    """RFC 5545: 한 줄 75바이트를 넘으면 접는다."""
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > 74:
            out.append(cur.decode("utf-8"))
            cur = b" "
        cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n".join(out)


def make_ics(notices, name):
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//HUFS Notice Bot//KO",
             "CALSCALE:GREGORIAN", f"X-WR-CALNAME:{_ics_escape(name)}", "X-WR-TIMEZONE:Asia/Seoul"]
    for n in notices:
        d = n["x"]["deadline"]
        day = dt.date.fromisoformat(d["date"])
        desc = (f"대상: {n['x']['who']}\n할 일: {n['x']['what']}\n"
                f"근거: {d['evidence']}\n{n['url']}")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{n['id']}@hufs-notice-bot",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
            f"DTEND;VALUE=DATE:{day + dt.timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{_ics_escape('[마감' + (' ' + d['time'] if d['time'] else '') + '] ' + n['x']['topic'])}",
            f"DESCRIPTION:{_ics_escape(desc)}",
            f"URL:{n['url']}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


def _norm(title):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", title)


def _duplicates(store):
    """국제교류처에서 기타로 분류된 공지가 다른 게시판에 이미 있으면(제목이 비슷하고 7일 안) 빼 둔다."""
    others = [n for n in store.values() if n["board"] != "국제교류" and not n.get("deleted")]
    dup = set()
    for key, n in store.items():
        if n["board"] != "국제교류" or n.get("x", {}).get("categories") != ["기타"]:
            continue
        day = dt.date.fromisoformat(n["date"])
        for o in others:
            if (abs((dt.date.fromisoformat(o["date"]) - day).days) <= 7 and
                    difflib.SequenceMatcher(None, _norm(n["title"]), _norm(o["title"])).ratio() >= 0.6):
                dup.add(key)
                break
    return dup


def build(store, log=print):
    SITE.mkdir(exist_ok=True)
    (SITE / "calendar").mkdir(exist_ok=True)
    items = []
    dup = _duplicates(store)
    for key, n in store.items():
        x = n.get("x")
        if not x or n.get("deleted") or key in dup:
            continue
        items.append({
            "key": key, "board": n["board"], "title": n["title"], "url": n["url"],
            "date": n["date"], "writer": n["writer"], "fixed": n.get("fixed", False),
            "files": len(n.get("files", [])), "changed": n.get("changed", ""), **{k: v for k, v in x.items() if k != "hash"},
        })
    items.sort(key=lambda i: i["date"], reverse=True)

    today = dt.date.today().isoformat()
    upcoming = [n for k, n in store.items() if not n.get("deleted") and k not in dup and n.get("x", {}).get("deadline")
                and n["x"]["deadline"]["date"] >= today]
    for fname, label, keep in [
        ("all.ics", "훕스 공지비서 · 전체 마감", lambda n: True),
        ("seoul.ics", "훕스 공지비서 · 서울캠퍼스", lambda n: n["x"]["campus"] in ("서울", "공통")),
        ("global.ics", "훕스 공지비서 · 글로벌캠퍼스", lambda n: n["x"]["campus"] in ("글로벌", "공통")),
    ]:
        (SITE / "calendar" / fname).write_text(
            make_ics([n for n in upcoming if keep(n)], label), encoding="utf-8", newline="")

    payload = json.dumps({"updated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "items": items},
                         ensure_ascii=False).replace("</", "<\\/")
    page = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", payload)
    (SITE / "index.html").write_text(page, encoding="utf-8")
    log(f"페이지 생성: {SITE / 'index.html'} (공지 {len(items)}건, 다가오는 마감 {len(upcoming)}건)")
