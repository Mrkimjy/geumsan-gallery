#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
김진영 Space — 뉴스 자동 수집/갱신 스크립트
index.html 안의 NEWS:START ~ NEWS:END 블록만 교체한다.
외부 라이브러리·API 키 없이 표준 라이브러리만 사용.

1순위: 언론사 공식 RSS (원문 URL + 실제 기사 요약이 들어있음)
2순위: Google 뉴스 RSS (1순위로 건수를 못 채웠을 때만)
"""
import html
import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
TARGET = os.environ.get("NEWS_TARGET", "index.html")
PER_CATEGORY = 3
MAX_AGE_DAYS = 5
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# ── 언론사 공식 RSS ──────────────────────────────────────────────
FEEDS = [
    ("연합뉴스", "https://www.yna.co.kr/rss/economy.xml"),
    ("연합뉴스", "https://www.yna.co.kr/rss/industry.xml"),
    ("매일경제", "https://www.mk.co.kr/rss/30100041/"),
    ("매일경제", "https://www.mk.co.kr/rss/50100032/"),
    ("매일경제", "https://www.mk.co.kr/rss/50200011/"),
    ("매일경제", "https://www.mk.co.kr/rss/50300009/"),
]

# ── 카테고리 정의 ────────────────────────────────────────────────
CATEGORIES = {
    "HR": {
        "include": r"(조직문화|기업문화|정기인사|임원인사|인사제도|채용|구인|이직|퇴사|리더십|인재|임금|연봉|노사|노조|파업|복지|근로|정년|주4일|워라밸|직장|사내문화|교육훈련|승진|인사평가|일자리|고용|HR)",
        "exclude": r"(연예|스포츠|야구|축구|변호사|검찰|소송|구속)",
        "gnews": [
            '조직문화 OR 기업문화 OR 인사제도 when:4d',
            '리더십 OR 인재육성 OR "일하는 방식" 직장 when:4d',
            '채용 OR 이직 OR 정년연장 when:4d',
        ],
        "emoji": ["📊", "👥", "🎓", "🤝", "💼"],
        "bg": ["#e8f0fe", "#f3e8ff", "#fef3c7", "#e0f2fe", "#fce7f3"],
    },
    "Tire": {
        "include": r"(타이어|한국타이어|금호타이어|넥센타이어|완성차|자동차|현대차|기아|르노코리아|KG모빌리티|전기차|하이브리드|모빌리티|자동차부품|車)",
        "exclude": r"(반도체|HBM|메모리|디스플레이|배터리 소재|조선|방산|연예|스포츠)",
        "gnews": [
            '한국타이어 OR 금호타이어 OR 넥센타이어 when:5d',
            '타이어 공장 OR 수출 OR 전기차용 when:5d',
            '완성차 OR 자동차부품 산업 when:3d',
        ],
        "emoji": ["🛞", "🚗", "🏭", "⚡", "🌏"],
        "bg": ["#fee2e2", "#e0e7ff", "#ecfdf5", "#fff7ed", "#eef2ff"],
    },
    "Econ": {
        "include": r"(재테크|연금|ISA|절세|코스피|코스닥|환율|금리|증시|주식|펀드|ETF|리츠|부동산|집값|전세|청약|대출|예금|적금|세금|배당|물가|가계)",
        "exclude": r"(사기|도박|연예|스포츠)",
        "gnews": [
            '재테크 OR 연금저축 OR ISA OR 절세 when:4d',
            '코스피 OR 환율 OR 기준금리 when:2d',
            '부동산 OR 청약 OR 대출규제 when:3d',
        ],
        "emoji": ["💰", "📈", "🏦", "🏠", "💳"],
        "bg": ["#ecfdf5", "#fef9c3", "#e0f2fe", "#f1f5f9", "#fae8ff"],
    },
}

BOILERPLATE = r"(Google 뉴스|전세계 매체|구독하기|무단전재|재배포 금지|기사제보)"

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def fetch(url, timeout=12):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
        return r.read()


def strip_tags(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def norm(t):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", t or "")


def clamp(s, n=110):
    s = (s or "").strip()
    if len(s) <= n:
        return s
    cut = s[:n]
    for sep in ("다. ", ". ", " "):
        i = cut.rfind(sep)
        if i > n * 0.6:
            return cut[:i + 1].strip() + "…"
    return cut.strip() + "…"


def good_summary(desc, title):
    """요약이 제목 복사본이거나 상투어면 버린다."""
    d, t = norm(desc), norm(title)
    if not d or len(d) < 35:
        return None
    if re.search(BOILERPLATE, desc):
        return None
    if d.startswith(t[:20]) and len(d) < len(t) + 25:
        return None
    return desc


def parse_rss(raw, default_source):
    root = ET.fromstring(raw)
    out = []
    for item in root.iter("item"):
        title = strip_tags(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        desc = strip_tags(item.findtext("description"))
        src_el = item.find("source")
        source = (src_el.text or "").strip() if src_el is not None and src_el.text else default_source
        pub = item.findtext("pubDate") or item.findtext(
            "{http://purl.org/dc/elements/1.1/}date")
        try:
            dt = parsedate_to_datetime(pub)
            dt = dt.astimezone(KST) if dt.tzinfo else dt.replace(tzinfo=KST)
        except Exception:
            dt = NOW
        if title and link:
            out.append({"title": title, "url": link, "desc": desc,
                        "source": source, "dt": dt})
    return out


def load_feeds():
    pool = []
    for name, url in FEEDS:
        try:
            pool += parse_rss(fetch(url), name)
        except Exception as e:
            print(f"  ! 피드 실패 {name} {url}: {e}", file=sys.stderr)
    print(f"공식 RSS 수집: {len(pool)}건")
    return pool


def google_news(query):
    url = ("https://news.google.com/rss/search?q="
           + urllib.parse.quote(query) + "&hl=ko&gl=KR&ceid=KR:ko")
    items = parse_rss(fetch(url), "Google 뉴스")
    for it in items:
        s = it["source"]
        if s and it["title"].endswith(" - " + s):
            it["title"] = it["title"][: -(len(s) + 3)].strip()
        it["desc"] = ""          # 구글 요약은 제목 복사본이라 버림
        it["via_gnews"] = True
    return items


def pick(cat, cfg, feed_pool):
    inc = re.compile(cfg["include"])
    exc = re.compile(cfg["exclude"])
    fresh = NOW - timedelta(days=MAX_AGE_DAYS)

    def ok(it):
        t = it["title"]
        return (it["dt"] >= fresh and inc.search(t)
                and not exc.search(t + " " + it["desc"]))

    cands = sorted([i for i in feed_pool if ok(i)],
                   key=lambda x: x["dt"], reverse=True)

    # 공식 RSS로 부족하면 구글 뉴스로 보충
    if len(cands) < PER_CATEGORY:
        for q in cfg["gnews"]:
            try:
                cands += [i for i in google_news(q) if i["dt"] >= fresh]
            except Exception as e:
                print(f"  ! 구글뉴스 실패 ({cat}): {e}", file=sys.stderr)
            if len(cands) >= PER_CATEGORY * 3:
                break

    cards, seen = [], set()
    for it in cands:
        if len(cards) >= PER_CATEGORY:
            break
        k = norm(it["title"])[:28]
        if not k or k in seen:
            continue
        seen.add(k)
        summary = good_summary(it["desc"], it["title"]) or (
            f"{it['source']} 보도. 카드를 눌러 원문에서 자세한 내용을 확인하세요.")
        i = len(cards)
        cards.append({
            "emoji": cfg["emoji"][i % len(cfg["emoji"])],
            "bg": cfg["bg"][i % len(cfg["bg"])],
            "source": clamp(it["source"], 20),
            "headline": clamp(it["title"], 58),
            "summary": clamp(summary, 108),
            "date": it["dt"].strftime("%Y.%m.%d"),
            "isNew": (NOW - it["dt"]) <= timedelta(hours=30),
            "url": it["url"],
        })
    return cards


def js(s):
    return json.dumps(s, ensure_ascii=False)


def render_block(data, updated):
    L = ["// <!--NEWS:START--> 이 블록은 GitHub Actions가 매일 자동 갱신합니다. 직접 수정 시 다음 실행에 덮어써집니다.",
         "const newsData = {", f"  updated: {js(updated)},"]
    for cat in ("HR", "Tire", "Econ"):
        L.append(f"  {cat}: [")
        for c in data[cat]:
            L.append("    { emoji:%s, bg:%s, source:%s, headline:%s, summary:%s, date:%s, isNew:%s, url:%s },"
                     % (js(c["emoji"]), js(c["bg"]), js(c["source"]), js(c["headline"]),
                        js(c["summary"]), js(c["date"]),
                        "true" if c["isNew"] else "false", js(c["url"])))
        L.append("  ],")
    L += ["};", "// <!--NEWS:END-->"]
    return "\n".join(L)


def main():
    feed_pool = load_feeds()
    data = {}
    for cat, cfg in CATEGORIES.items():
        cards = pick(cat, cfg, feed_pool)
        print(f"[{cat}] {len(cards)}건")
        for c in cards:
            print(f"   - ({c['date']}) {c['source']} | {c['headline'][:42]}")
            print(f"       {c['summary'][:60]}")
        data[cat] = cards

    if any(len(v) < PER_CATEGORY for v in data.values()):
        print("건수 부족 → 기존 내용 유지 (갱신 취소)", file=sys.stderr)
        return 1

    src = open(TARGET, encoding="utf-8").read()
    a = src.index("// <!--NEWS:START-->")
    b = src.index("// <!--NEWS:END-->") + len("// <!--NEWS:END-->")
    updated = NOW.strftime("%Y.%m.%d %H:%M")
    new = src[:a] + render_block(data, updated) + src[b:]
    if new == src:
        print("변경 없음")
        return 0
    open(TARGET, "w", encoding="utf-8").write(new)
    print(f"갱신 완료: {updated} KST")
    return 0


if __name__ == "__main__":
    sys.exit(main())
