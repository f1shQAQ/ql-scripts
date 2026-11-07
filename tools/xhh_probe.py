#!/usr/bin/env python3
"""小黑盒签到诊断工具（只在本地跑，不进青龙）。

用途：Web 通道能读到任务列表、但签到返回「非法的请求」时，用同一账号依次尝试几种
参数组合，把服务端的真实返回打出来，定位到底是哪一项被风控卡住。

用法：
    python tools/xhh_probe.py --cookie "pkey=xxx;x_xhh_tokenid=xxx"
    # 或者不传 --cookie，运行后按提示粘贴

注意：这个脚本会真的调签到接口（成功的话今天就签完了，再调会返回「已签到」），
每种组合之间间隔 1 秒。Cookie 只在本机使用，不要贴到公开地方。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
import uuid
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qlutils import parse_cookie  # noqa: E402
from checkin_xiaoheihe import API, WEB_PROFILE, WEB_UA, heybox_id_from_pkey, web_sign  # noqa: E402

MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 13; M2012K11AC Build/TKQ1.220829.002) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)


def stable_device_id(pkey: str) -> str:
    return str(uuid.UUID(hashlib.sha1(pkey.encode()).hexdigest()[:32]))


def main() -> int:
    parser = argparse.ArgumentParser(description="小黑盒签到诊断")
    parser.add_argument("--cookie", help="XHH_COOKIE 的内容")
    parser.add_argument("--only-read", action="store_true", help="只读任务列表，不尝试签到")
    args = parser.parse_args()

    raw = args.cookie
    if not raw:
        raw = input("粘贴 XHH_COOKIE（pkey=...），回车确认：\n").strip()
    fields = parse_cookie(raw)
    pkey = fields.get("pkey") or fields.get("user_pkey", "")
    if not pkey:
        print("Cookie 里没有 pkey")
        return 1
    heybox_id = heybox_id_from_pkey(pkey)
    token_id = fields.get("x_xhh_tokenid", "")
    device_id = stable_device_id(pkey)
    print(f"heybox_id={heybox_id}  tokenid={'有' if token_id else '无'}  device_id={device_id}")

    base_cookies = {"pkey": pkey, "user_pkey": pkey,
                    "heybox_id": heybox_id, "user_heybox_id": heybox_id}
    if token_id:
        base_cookies["x_xhh_tokenid"] = token_id

    def call(path: str, profile: dict, cookies: dict, ua: str = WEB_UA, extra: dict | None = None) -> tuple[str, str]:
        query = {**profile, "heybox_id": heybox_id, **web_sign(path), **(extra or {})}
        query = {k: v for k, v in query.items() if v}
        headers = {
            "User-Agent": ua,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://xiaoheihe.cn/",
            "Origin": "https://xiaoheihe.cn",
        }
        r = requests.get(API + path, params=query, headers=headers, cookies=cookies, timeout=20)
        try:
            data = r.json()
            return str(data.get("status")), str(data.get("msg") or data.get("result") or "")
        except ValueError:
            return f"HTTP {r.status_code}", r.text[:80]

    print("\n[0] 先确认登录态：/task/list_v2/")
    status, msg = call("/task/list_v2/", dict(WEB_PROFILE), base_cookies)
    print(f"    {status} / {msg[:120]}")
    if args.only_read:
        return 0
    if status != "ok":
        print("登录态都不通，先解决 Cookie 问题")
        return 1

    website_profile = {
        "os_type": "web", "app": "heybox",
        "x_app": "heybox_website", "x_client_type": "weboutapp",
        "x_os_type": "Windows", "web_version": "2.5", "device_id": device_id,
    }
    account_profile = {
        "os_type": "web", "app": "heybox",
        "x_app": "heybox_acc", "client_type": "acc_mobile",
        "x_client_type": "web", "x_os_type": "Windows",
    }

    variants: list[tuple[str, dict, dict, str, dict | None]] = [
        ("A 基线（当前脚本用的）", dict(WEB_PROFILE), base_cookies, WEB_UA, None),
        ("B 只带 pkey 一个 Cookie", dict(WEB_PROFILE), {"pkey": pkey}, WEB_UA, None),
        ("C 基线 + device_id", {**WEB_PROFILE, "device_id": device_id}, base_cookies, WEB_UA, None),
        ("D 官网参数组 heybox_website", website_profile, base_cookies, WEB_UA, None),
        ("E 账号中心参数组 heybox_acc", account_profile, base_cookies, WEB_UA, None),
        ("F 手机 UA", dict(WEB_PROFILE), base_cookies, MOBILE_UA, None),
        ("G 基线 + task_id=1", dict(WEB_PROFILE), base_cookies, WEB_UA, {"task_id": "1"}),
    ]
    if not token_id:
        variants.append(("H 不带 tokenid 对照（当前就是）", dict(WEB_PROFILE), base_cookies, WEB_UA, None))

    print("\n依次尝试 /task/sign_v3/sign：")
    for label, profile, cookies, ua, extra in variants:
        status, msg = call("/task/sign_v3/sign", profile, cookies, ua, extra)
        print(f"  {label:34} -> {status} / {msg[:90]}")
        if status == "ok":
            print("\n  >>> 这组参数能签到，记下来 <<<")
            break
        time.sleep(1)

    print("\n把上面整段输出发给作者即可定位。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
