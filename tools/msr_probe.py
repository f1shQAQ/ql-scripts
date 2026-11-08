#!/usr/bin/env python3
"""Microsoft Rewards 登录态自检：判断这份 Cookie 还能不能用，以及问题出在导出还是脚本。

用法：
    MSR_COOKIE='...' python tools/msr_probe.py
    python tools/msr_probe.py cookies.json        # 直接读文件，不用往环境变量里塞
    python tools/msr_probe.py 'a=1; b=2'          # 也可以直接传 Cookie 字符串

检查四项：
    1. Cookie 概况：各域名数量、关键登录 Cookie 是否齐全（值打码）
    2. getuserinfo：用 requests 和 curl_cffi 各请求一次，对比状态码
    3. /earn：是否被 302 到登录页
    4. 静默登录：能不能用 login.live.com 的登录态续登（只 GET，不提交表单）

这个脚本只读：不发通知、不改环境变量、不上报任何任务。
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

import checkin_msrewards as msr  # noqa: E402
from qlutils import mask, parse_cookie_export  # noqa: E402

KEY_COOKIES = (
    "_U",
    "RPSSecAuth",
    "MSPAuth",
    "MSPProf",
    "_C_Auth",
    "ANON",
    "WLSSC",
    "MSPPre",
    "MSPRequ",
    "SRCHUID",
    "MUID",
)


def read_cookie() -> str:
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        path = Path(arg)
        if path.is_file():
            return path.read_text(encoding="utf-8")
        return arg
    return os.getenv("MSR_COOKIE", "").strip()


def build_session(kind: str, cookies: list[dict], profile) -> object:
    if kind == "curl":
        from curl_cffi import requests as curl_requests

        session = curl_requests.Session(impersonate="chrome")
    else:
        session = requests.Session()
    session.headers.update({"User-Agent": profile.ua, "Accept-Language": profile.language})
    for c in cookies:
        session.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"], secure=c["secure"])
    return session


def probe_userinfo(session, profile) -> str:
    try:
        r = session.get(
            f"{msr.REWARDS}/api/getuserinfo",
            params={"type": 1, "X-Requested-With": "XMLHttpRequest", "_": int(time.time() * 1000)},
            headers=msr.xhr_headers(profile, f"{msr.REWARDS}/"),
            timeout=20,
        )
    except Exception as e:  # noqa: BLE001
        return f"请求异常 {type(e).__name__}: {e}"
    if r.status_code != 200:
        return f"HTTP {r.status_code}"
    try:
        data = r.json()
    except ValueError:
        return f"HTTP 200 但不是 JSON：{r.text[:80]!r}"
    dash = data.get("dashboard") or data
    status = (dash.get("userStatus") or {}) if isinstance(dash, dict) else {}
    if not status:
        return "HTTP 200 但拿不到 userStatus"
    level = (status.get("levelInfo") or {}).get("activeLevel", "?")
    return f"正常，{status.get('availablePoints')} 分，等级 {level}"


def probe_earn(session, profile) -> str:
    try:
        r = session.get(
            f"{msr.REWARDS}/earn",
            headers=msr.nav_headers(profile, f"{msr.BING}/", "same-site"),
            timeout=20,
            allow_redirects=False,
        )
    except Exception as e:  # noqa: BLE001
        return f"请求异常 {type(e).__name__}: {e}"
    if r.status_code in (301, 302, 303, 307, 308):
        return f"HTTP {r.status_code} → {(r.headers.get('location') or '')[:110]}"
    return f"HTTP {r.status_code}，页面 {len(r.text)} 字节"


def probe_silent(session, profile) -> str:
    try:
        r = session.get(
            f"{msr.REWARDS}/Signin",
            params={"idru": "/", "silent": "True"},
            headers=msr.nav_headers(profile, f"{msr.REWARDS}/", "same-site"),
            timeout=20,
        )
    except Exception as e:  # noqa: BLE001
        return f"请求异常 {type(e).__name__}: {e}"
    url = str(r.url)
    if msr.login_redirect(url) or "rewards.bing.com" not in url:
        m = re.search(r"[?&]error=([\w_]+)", url) or re.search(r'name="error"[^>]*value="([^"]+)"', r.text)
        return f"停在 {url.split('/')[2] if '://' in url else url[:40]}（{m.group(1) if m else '没有 error 参数'}）"
    if re.search(r"<form[^>]*action=", r.text, re.I):
        return "拿到登录表单，理论上可以静默续登"
    return f"跳回 {url[:80]}"


def main() -> int:
    raw = read_cookie()
    if not raw:
        print("没有 Cookie。把导出的内容放进环境变量 MSR_COOKIE，或者作为参数传进来。")
        return 2

    parts = [p.strip() for p in raw.split("&&") if p.strip()]
    if len(parts) > 1:
        print(f"注意：检测到 {len(parts)} 个账号，只检查第一个。\n")

    cookies = parse_cookie_export(parts[0], ".bing.com")
    if not cookies:
        print("解析不出任何 Cookie，检查导出格式。")
        return 2

    domains: dict[str, int] = {}
    for c in cookies:
        domains[c["domain"]] = domains.get(c["domain"], 0) + 1
    print("1. Cookie 概况")
    print("   " + "，".join(f"{d} {n} 个" for d, n in domains.items()))
    values = {c["name"]: c["value"] for c in cookies}
    for name in KEY_COOKIES:
        value = values.get(name)
        print(f"   {name:<12} {'有  ' + mask(value) if value else '缺失'}")
    missing = [n for n in ("_U", "RPSSecAuth", "MSPAuth", "_C_Auth") if n not in values]
    print()

    profile, _ = msr.device_set(values.get("MUID", ""))
    kinds = ["requests"]
    try:
        import curl_cffi  # noqa: F401

        kinds.append("curl")
    except ImportError:
        print("（没装 curl_cffi，只测 requests；要测 TLS 指纹先 pip install curl_cffi）\n")

    print("2. getuserinfo")
    results = {}
    for kind in kinds:
        session = build_session(kind, cookies, profile)
        result = probe_userinfo(session, profile)
        results[kind] = result
        print(f"   {kind:<9} {result}")
        # 同一个会话继续看 /earn 和静默登录
        if kind == "requests":
            requests_session = session
    print()

    print("3. /earn")
    print(f"   {probe_earn(requests_session, profile)}")
    print()

    print("4. 静默登录")
    print(f"   {probe_silent(requests_session, profile)}")
    print()

    print("结论")
    ok = [k for k, v in results.items() if v.startswith("正常")]
    if missing:
        print(f"   导出不完整：缺 {'、'.join(missing)}。重新导出，三处域名都要有。")
    if ok:
        print(f"   登录态可用（{'、'.join(ok)}）。网页端报错就把完整日志贴出来。")
        if len(ok) < len(results):
            bad = [k for k in results if k not in ok]
            print(f"   但 {'、'.join(bad)} 拿不到面板，可以设置 MSR_IMPERSONATE=off 再跑脚本。")
    else:
        print("   这组 Cookie 已经换不到登录态了，需要重新登录后导出：")
        print("   1) 浏览器退出登录，重新登录 rewards.bing.com，勾选「保持登录」")
        print("   2) 用 Cookie-Editor 分别在 rewards.bing.com 和 login.live.com 导出，两段拼起来")
        print("   3) 别让浏览器和脚本长期共用同一份登录态：跑脚本前先关掉 rewards 页面")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
