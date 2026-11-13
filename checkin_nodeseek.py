#!/usr/bin/env python3
"""
cron: 7 0 * * *
new Env('NodeSeek签到')

NodeSeek / DeepFlood 每日签到领鸡腿。

环境变量：
    NS_COOKIE   nodeseek.com 的 Cookie：请求头字符串，或 Cookie-Editor 导出的 JSON。
                多个账号用换行或 "&&" 分隔（JSON 导出的账号之间只能用 "&&"）。
    DF_COOKIE   deepflood.com 的 Cookie，可选，格式同上。
    NS_UA       导出 Cookie 的那个浏览器的 User-Agent，可选但强烈建议填，所有账号共用。
                cf_clearance 绑定 UA，UA 对不上会被 Cloudflare 拦。
    NS_RANDOM   "true" 随机鸡腿（默认），"false" 固定 5 个。
    NS_PROXY    签到请求走的代理，默认 http://127.0.0.1:20171（v2rayA 的 HTTP 端口）。
                填 "off" 不走代理直连。

依赖：curl_cffi（模拟浏览器 TLS 指纹过 Cloudflare）。没装时退回 requests，大概率被拦。

签到接口参考 NodeSeek Pro 油猴脚本（Woodll, GPL-3.0）：
    POST {site}/api/attendance?random=true|false
返回 JSON：success / gain / current / message。
"""

from __future__ import annotations

import os
import re
import sys
import time

from qlutils import env_bool, get_cookie_accounts, notify, parse_cookie_export

try:
    from curl_cffi import requests as http

    HAS_CURL = True
except ImportError:
    import requests as http

    HAS_CURL = False

SITES = {
    "NodeSeek": ("NS_COOKIE", "https://www.nodeseek.com"),
    "DeepFlood": ("DF_COOKIE", "https://www.deepflood.com"),
}
DEFAULT_PROXY = "http://127.0.0.1:20171"
ALREADY_RE = re.compile(r"已完成签到|重复|already", re.I)


def browser_profile(ua: str) -> tuple[str, dict[str, str]]:
    """根据 UA 选 curl_cffi 的指纹，并补齐与 UA 一致的请求头。

    UA 为空时返回 ("chrome", {})，由 curl_cffi 自带一致的 UA 和 Sec-Ch-Ua。
    """
    ua = ua.strip()
    if not ua:
        return "chrome", {}
    headers = {"User-Agent": ua}
    if "Firefox/" in ua:
        return "firefox", headers
    m = re.search(r"(?:Chrome|Chromium)/(\d+)", ua)
    if not m:  # Safari 等不发 Sec-Ch-Ua 的浏览器
        return ("safari" if "Safari/" in ua else "chrome"), headers
    major = m.group(1)
    edge = re.search(r"Edg/(\d+)", ua)
    brand = f'"Microsoft Edge";v="{edge.group(1)}"' if edge else f'"Google Chrome";v="{major}"'
    if "Windows" in ua:
        platform = "Windows"
    elif "Mac OS X" in ua or "Macintosh" in ua:
        platform = "macOS"
    elif "Android" in ua:
        platform = "Android"
    else:
        platform = "Linux"
    headers.update(
        {
            "Sec-Ch-Ua": f'"Chromium";v="{major}", "Not?A_Brand";v="24", {brand}',
            "Sec-Ch-Ua-Mobile": "?1" if "Mobile" in ua else "?0",
            "Sec-Ch-Ua-Platform": f'"{platform}"',
        }
    )
    return "chrome", headers


IMPERSONATE, UA_HEADERS = browser_profile(os.getenv("NS_UA", ""))


def get_proxy() -> str:
    """NS_PROXY 没设置时用默认代理，"off" / "none" / "false" 表示直连。"""
    value = os.getenv("NS_PROXY", "").strip() or DEFAULT_PROXY
    return "" if value.lower() in {"off", "none", "false", "0"} else value


PROXY = get_proxy()


def to_header(text: str, base: str) -> str:
    """JSON 导出 / cookies.txt / 请求头统一转成请求头字符串。"""
    cookies = parse_cookie_export(text, base.split("//", 1)[1])
    if not cookies:
        raise ValueError("Cookie 为空或格式不对")
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


def checkin(base: str, cookie: str, random: bool) -> tuple[str, str]:
    """返回 (状态, 说明)；状态为 ok / already / fail。"""
    url = f"{base}/api/attendance?random={'true' if random else 'false'}"
    headers = {
        **UA_HEADERS,
        "Cookie": cookie,
        "Origin": base,
        "Referer": f"{base}/board",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
    }
    kw = {"impersonate": IMPERSONATE} if HAS_CURL else {}
    if PROXY:
        kw["proxies"] = {"http": PROXY, "https": PROXY}
    if not HAS_CURL and "User-Agent" not in headers:
        headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        )
    last = ""
    for attempt in range(1, 4):
        try:
            r = http.post(url, headers=headers, timeout=30, **kw)
        except Exception as e:  # noqa: BLE001  网络错误，重试
            last = f"请求异常：{e}"
            if PROXY:
                last += f"（代理 {PROXY} 是否可用？不需要代理就设 NS_PROXY=off）"
        else:
            try:
                data = r.json()
            except ValueError:
                if r.status_code in (403, 503) or "cloudflare" in r.text.lower():
                    last = f"HTTP {r.status_code}，被 Cloudflare 拦截"
                    if not HAS_CURL:
                        last += "（没装 curl_cffi，请在依赖管理里安装）"
                    else:
                        last += "（检查 NS_UA 是否与导出 Cookie 的浏览器一致，或重新导出 Cookie）"
                else:
                    last = f"HTTP {r.status_code}，返回不是 JSON：{r.text[:120]!r}"
            else:
                msg = str(data.get("message") or "")
                if data.get("success"):
                    return "ok", f"+{data.get('gain')} 🍗，共 {data.get('current')} 🍗"
                if ALREADY_RE.search(msg):
                    return "already", msg
                if r.status_code in (401, 403) or re.search(r"登录|USER NOT FOUND", msg, re.I):
                    return "fail", f"Cookie 失效：{msg or r.status_code}"
                last = msg or f"HTTP {r.status_code}，签到失败"
        print(f"  第 {attempt} 次失败：{last}")
        if attempt < 3:
            time.sleep(5 * attempt)
    return "fail", last


def main() -> None:
    print(f"传输层：{'curl_cffi（' + IMPERSONATE + ' 指纹）' if HAS_CURL else 'requests（未装 curl_cffi）'}")
    print(f"代理：{PROXY or '不走代理'}")
    random = env_bool("NS_RANDOM", True)
    results: list[str] = []
    failed = total = 0
    for name, (env, base) in SITES.items():
        for i, raw in enumerate(get_cookie_accounts(env), 1):
            if total:
                time.sleep(3)
            total += 1
            label = f"{name} 账号{i}"
            print(f"[{label}] 签到中…")
            try:
                status, info = checkin(base, to_header(raw, base), random)
            except Exception as e:  # noqa: BLE001
                status, info = "fail", f"运行出错：{type(e).__name__}: {e}"
            icon = {"ok": "✅", "already": "☑️", "fail": "❌"}[status]
            results.append(f"{icon} {label}：{info}")
            failed += status == "fail"

    if not total:
        print("没有设置环境变量 NS_COOKIE 或 DF_COOKIE，跳过。")
        return
    notify("NodeSeek签到", "\n".join(results))
    if failed == total:
        sys.exit(1)


if __name__ == "__main__":
    main()
