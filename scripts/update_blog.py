#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PeConnecton — 네이버 블로그 최근 글 2개(제목·날짜·링크)를 blog.json 으로 저장."""
import json, os, re, ssl, sys, urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

KST = timezone(timedelta(hours=9))
BLOG_ID = "a840127"
TARGET = os.environ.get("BLOG_TARGET", "blog.json")
COUNT = 2
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
ctx = ssl.create_default_context()


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
        return r.read()


def main():
    raw = fetch(f"https://rss.blog.naver.com/{BLOG_ID}.xml")
    root = ET.fromstring(raw)
    posts = []
    for it in root.iter("item"):
        title = re.sub(r"\s+", " ", (it.findtext("title") or "")).strip()
        link = (it.findtext("link") or "").strip().split("?")[0]
        try:
            dt = parsedate_to_datetime(it.findtext("pubDate")).astimezone(KST)
        except Exception:
            continue
        if title and link:
            posts.append({"title": title, "url": link, "date": dt.strftime("%Y.%m.%d"), "_dt": dt})
    posts.sort(key=lambda p: p["_dt"], reverse=True)
    posts = [{k: v for k, v in p.items() if k != "_dt"} for p in posts[:COUNT]]
    if not posts:
        print("글 없음 → 기존 유지", file=sys.stderr)
        return 1
    new = json.dumps({"posts": posts}, ensure_ascii=False, indent=1) + "\n"
    try:
        old = open(TARGET, encoding="utf-8").read()
    except FileNotFoundError:
        old = ""
    if new != old:
        open(TARGET, "w", encoding="utf-8").write(new)
        print("갱신:", [p["title"] for p in posts])
    else:
        print("변경 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
