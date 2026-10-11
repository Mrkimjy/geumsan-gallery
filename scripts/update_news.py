#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PeConnecton — 뉴스 자동 수집/갱신 스크립트
news.json 파일만 갱신한다. (index.html은 건드리지 않음 → 수동 업로드와 충돌 없음)
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
TARGET = os.environ.get("NEWS_TARGET", "news.json")
PER_CATEGORY = 3
MAX_AGE_DAYS = 4
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
        # (정규식, 가중치) — 제목에 걸린 가중치 합이 높을수록 우선. MIN_SCORE 미만은 제외
        "weights": [
            (r"조직문화|기업문화|사내문화|일하는 방식", 10),
            (r"리더십|세대|MZ|꼰대|소통", 8),
            (r"인사제도|인사평가|승진|성과급|임금체계|정년|주4일|주 4일|근로시간|워라밸", 7),
            (r"채용|신입|공채|이직|퇴사|인재|교육훈련|HR", 5),
            (r"노사|노조|임단협|파업|임금협상|복지", 4),
            (r"직장|근로|일자리|고용", 2),
        ],
        "exclude": r"(연예|스포츠|야구|축구|변호사|검찰|소송|구속|건설 임시|일용직|코스피|부동산)",
        "gnews": [
            '조직문화 OR 기업문화 OR 세대갈등 when:4d',
            '리더십 OR 인사제도 OR 성과급 when:4d',
            '채용 OR 정년연장 OR 주4일제 when:4d',
        ],
        "emoji": ["👥", "🤝", "🎓", "📋", "💼"],
    },
    "Tire": {
        "weights": [
            (r"한국타이어|금호타이어|넥센타이어|한온시스템", 14),
            (r"타이어", 12),
            (r"자동차부품|부품사|완성차|현대차|기아|현대모비스|르노코리아|KG모빌리티|GM", 4),
            (r"전기차|하이브리드|모빌리티|수소차|자율주행", 4),
            (r"자동차|車|차량|관세|수출", 2),
        ],
        "exclude": r"(반도체|HBM|메모리|디스플레이|배터리 소재|조선|방산|연예|스포츠|시승기|출시 행사|할인)",
        "gnews": [
            '한국타이어 OR 금호타이어 OR 넥센타이어 when:5d',
            '타이어 산업 OR 타이어 수출 OR 전기차용 타이어 when:5d',
            '자동차부품 OR 완성차 관세 when:3d',
        ],
        "emoji": ["🛞", "🚗", "🏭", "⚡", "🌏"],
    },
    "Econ": {
        "weights": [
            (r"코스피|코스닥|환율|기준금리|금리|증시|국채", 9),
            (r"연금|IRP|ISA|절세|세액공제|배당|ETF|펀드|리츠", 8),
            (r"집값|전세|청약|대출|부동산|아파트|주담대", 7),
            (r"재테크|예금|적금|물가|가계|세금|주식|투자", 4),
        ],
        "exclude": r"(사기|도박|연예|스포츠|코인|비트코인)",
        "gnews": [
            '코스피 OR 환율 OR 기준금리 when:2d',
            '연금저축 OR ISA OR 절세 OR 세액공제 when:4d',
            '부동산 OR 청약 OR 대출규제 when:3d',
        ],
        "emoji": ["📈", "💰", "🏦", "🏠", "💳"],
    },
}
MIN_SCORE = 4

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


def score(title, cfg):
    return sum(w for pat, w in cfg["weights"] if re.search(pat, title))


def pick(cat, cfg, feed_pool):
    exc = re.compile(cfg["exclude"])
    fresh = NOW - timedelta(days=MAX_AGE_DAYS)

    cands = [i for i in feed_pool if i["dt"] >= fresh]
    # 공식 RSS에 더해 구글 뉴스도 항상 보충 (카테고리 적합도 확보)
    for q in cfg["gnews"]:
        try:
            cands += [i for i in google_news(q) if i["dt"] >= fresh]
        except Exception as e:
            print(f"  ! 구글뉴스 실패 ({cat}): {e}", file=sys.stderr)

    scored = []
    for it in cands:
        if exc.search(it["title"] + " " + it["desc"]):
            continue
        sc = score(it["title"], cfg)
        if sc < MIN_SCORE:
            continue
        # 공식 RSS(요약 있음)에 약간 가산, 최신일수록 가산
        age_h = max(0, (NOW - it["dt"]).total_seconds() / 3600)
        sc += 1.5 if not it.get("via_gnews") else 0
        sc += max(0, 3 - age_h / 24)
        scored.append((sc, it))
    scored.sort(key=lambda x: x[0], reverse=True)

    cards, seen, src_count = [], set(), {}
    for _, it in scored:
        if len(cards) >= PER_CATEGORY:
            break
        k = norm(it["title"])[:28]
        if not k or k in seen:
            continue
        # 한 매체가 카테고리를 독점하지 않도록 최대 2건
        if src_count.get(it["source"], 0) >= 2:
            continue
        seen.add(k)
        src_count[it["source"]] = src_count.get(it["source"], 0) + 1
        summary = good_summary(it["desc"], it["title"]) or (
            f"{it['source']} 보도. 카드를 눌러 원문에서 자세한 내용을 확인하세요.")
        i = len(cards)
        cards.append({
            "emoji": cfg["emoji"][i % len(cfg["emoji"])],
            "source": clamp(it["source"], 20),
            "headline": clamp(it["title"], 58),
            "summary": clamp(re.sub(r"^\([^)]*\)\s*[^=]{0,12}=\s*", "", summary), 108),
            "date": it["dt"].strftime("%Y.%m.%d"),
            "isNew": (NOW - it["dt"]) <= timedelta(hours=30),
            "url": it["url"],
        })
    return cards


def js(s):
    return json.dumps(s, ensure_ascii=False)


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

    updated = NOW.strftime("%Y.%m.%d %H:%M")
    out = {"updated": updated, **data}
    new = json.dumps(out, ensure_ascii=False, indent=1) + "\n"
    try:
        old = open(TARGET, encoding="utf-8").read()
    except FileNotFoundError:
        old = ""
    # 시각만 바뀐 경우는 커밋하지 않도록 비교
    strip = lambda t: re.sub(r'"updated": "[^"]*"', "", t)
    if strip(new) == strip(old):
        print("변경 없음")
        return 0
    open(TARGET, "w", encoding="utf-8").write(new)
    print(f"갱신 완료: {updated} KST")
    return 0


if __name__ == "__main__":
    sys.exit(main())
