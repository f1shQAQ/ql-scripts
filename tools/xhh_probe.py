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
import json
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

    def call(path: str, profile: dict, cookies: dict, ua: str = WEB_UA, extra: dict | None = None) -> dict:
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
        except ValueError:
            return {"status": f"HTTP {r.status_code}", "msg": r.text[:80]}
        return data if isinstance(data, dict) else {"status": "bad_json", "msg": str(data)[:80]}

    def brief(data: dict) -> str:
        status = str(data.get("status"))
        detail = data.get("msg") or data.get("result") or ""
        if not isinstance(detail, str):
            detail = json.dumps(detail, ensure_ascii=False)
        return f"{status} / {detail[:90]}"

    print("\n[0] 登录态：/task/list_v2/")
    listing = call("/task/list_v2/", dict(WEB_PROFILE), base_cookies)
    print(f"    {brief(listing)[:100]}")
    if args.only_read:
        return 0
    if listing.get("status") != "ok":
        print("登录态都不通，先解决 Cookie 问题")
        return 1

    # 把「签到」任务的原始字段打出来，看看接口有没有要求 task_id 之类
    sign_task: dict = {}
    for group in (listing.get("result") or {}).get("task_list") or []:
        for item in group.get("tasks") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "sign" or "签到" in str(item.get("title") or ""):
                sign_task = item
                break
        if sign_task:
            break
    if sign_task:
        keep = {k: v for k, v in sign_task.items()
                if k in ("id", "task_id", "type", "state", "title", "task_type", "jump_url", "report_extra")}
        print("\n[1] 签到任务字段：")
        print("    " + json.dumps(keep, ensure_ascii=False)[:600])
        print(f"    全部键名：{sorted(sign_task.keys())}")
    task_id = str(sign_task.get("id") or sign_task.get("task_id") or "")

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
        ("A 基线", dict(WEB_PROFILE), base_cookies, WEB_UA, None),
        ("B 基线 + token 放 query", dict(WEB_PROFILE), base_cookies, WEB_UA,
         {"token": token_id} if token_id else None),
        ("C 基线 + x_xhh_tokenid 放 query", dict(WEB_PROFILE), base_cookies, WEB_UA,
         {"x_xhh_tokenid": token_id} if token_id else None),
        ("D 官网参数组", website_profile, base_cookies, WEB_UA, None),
        ("E 账号中心参数组", account_profile, base_cookies, WEB_UA, None),
        ("F 手机 UA", dict(WEB_PROFILE), base_cookies, MOBILE_UA, None),
    ]
    if task_id:
        variants.insert(1, (f"B 基线 + task_id={task_id}", dict(WEB_PROFILE), base_cookies, WEB_UA,
                            {"task_id": task_id}))
    variants.append(("G 不带 _rnd", dict(WEB_PROFILE), base_cookies, WEB_UA, {"_rnd": "_"}))

    print("\n[2] 依次尝试 /task/sign_v3/sign：")
    for label, profile, cookies, ua, extra in variants:
        if extra and extra.get("_rnd") == "_":
            # 显式去掉 _rnd：先构造 query，再剔除
            query_extra = {}
            query = {**profile, "heybox_id": heybox_id, **web_sign("/task/sign_v3/sign"), **query_extra}
            query.pop("_rnd", None)
            query = {k: v for k, v in query.items() if v}
            r = requests.get(
                API + "/task/sign_v3/sign",
                params=query,
                headers={"User-Agent": ua, "Accept": "application/json, text/plain, */*",
                         "Referer": "https://xiaoheihe.cn/", "Origin": "https://xiaoheihe.cn"},
                cookies=cookies, timeout=20,
            )
            try:
                data = r.json()
            except ValueError:
                data = {"status": f"HTTP {r.status_code}", "msg": r.text[:80]}
        else:
            data = call("/task/sign_v3/sign", profile, cookies, ua, extra)
        print(f"  {label:28} -> {brief(data)}")
        if data.get("status") == "ok":
            print("\n  >>> 这组参数能签到 <<<")
            print("  返回：" + json.dumps(data.get("result"), ensure_ascii=False)[:300])
            break
        time.sleep(1)

    print("\n把上面整段输出发给作者即可定位。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
