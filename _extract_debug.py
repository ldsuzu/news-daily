"""临时：复现正文提取失败，并定位是哪一环断的。"""

import sys

sys.path.insert(0, r"D:\DSH工作区\news-daily\src")
sys.stdout = open(r"D:\DSH工作区\news-daily\data\_extract_debug.txt", "w", encoding="utf-8")

import httpx

from newspipe.collectors.base import Http
from newspipe.pipeline.extract import extract_for_url, extract_from_html

URLS = [
    "https://www.gamersky.com/news/202609/2215184.shtml",
    "https://www.3dmgame.com/news/202609/3954218.html",
    "https://www.solidot.org/story?sid=85452",
]

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def probe(label, http):
    print(f"--- {label} ---")
    for url in URLS:
        host = url.split("/")[2]
        try:
            resp = http.get(url)
            html = resp.text
        except Exception as exc:
            print(f"  {host:<18} 请求失败：{type(exc).__name__}: {str(exc)[:70]}")
            continue
        direct = extract_from_html(html)
        via = extract_for_url(url, http)
        print(f"  {host:<18} HTTP {resp.status_code}  HTML {len(html):>7} 字   "
              f"直接抽 {len(direct):>5} 字   extract_for_url {len(via):>5} 字")
    print()


probe("默认 UA（NewsPipe/0.1）", Http(timeout=20))
probe("浏览器 UA", Http(timeout=20, user_agent=BROWSER_UA))

# 用裸 httpx 对照，排除 Http 封装的干扰
print("--- 裸 httpx + 浏览器 UA ---")
with httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": BROWSER_UA}) as c:
    for url in URLS:
        host = url.split("/")[2]
        r = c.get(url)
        print(f"  {host:<18} HTTP {r.status_code}  HTML {len(r.text):>7} 字   抽出 {len(extract_from_html(r.text)):>5} 字")
