"""훕스 공지비서 한 번에 실행: 수집 → 추출 → 페이지 생성.

    python run.py              # 게시판마다 2페이지 수집, 규칙 기반 추출
    python run.py --pages 5    # 더 많이 수집
    python run.py --ai         # Claude로 추출 (ANTHROPIC_API_KEY 필요)
    python run.py --no-collect # 수집 없이 페이지만 다시 만들기
"""
import argparse

import build
import collect
import extract


def main():
    p = argparse.ArgumentParser(description="훕스 공지비서")
    p.add_argument("--pages", type=int, default=2, help="게시판마다 읽을 목록 페이지 수")
    p.add_argument("--ai", action="store_true", help="Claude API로 추출")
    p.add_argument("--no-collect", action="store_true", help="수집 단계 건너뛰기")
    a = p.parse_args()

    store = collect.load() if a.no_collect else collect.collect(pages=a.pages)
    store = extract.enrich(store, use_ai=a.ai)
    collect.save(store)
    build.build(store)


if __name__ == "__main__":
    main()
