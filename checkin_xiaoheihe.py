#!/usr/bin/env python3
"""
cron: 45 8 * * *
new Env('小黑盒签到')

小黑盒每日签到：App 通道签到（/task/sign_v3/sign），顺便汇报每日任务完成情况和 H 币余额。

环境变量：
    XHH_COOKIE     App 抓包拿到的 Cookie + 设备字段，格式：
                   pkey=xxx;x_xhh_tokenid=xxx;imei=xxx;device_info=xxx;os_version=16;dw=411;channel=heybox_xiaomi
                   必须有 pkey 和 x_xhh_tokenid（身份），以及 imei、device_info、os_version、dw（设备）。
                   heybox_id 从 pkey 自动解析，不用填。多个账号用换行或 "&&" 分隔。
    XHH_HKEY_API   计算 App 签名（hkey/_rnd）的服务地址，默认 https://hkey.qcciii.com/hkey。
                   这是第三方服务：它只会收到 heybox_id、imei、接口路径和时间戳，收不到 Cookie；
                   但服务挂了脚本就不能用。可以自己搭一个同协议的服务换掉。

接口参考 yowiv08/heybox（GitHub）的 heybox_sign.js。
"""

from __future__ import annotations

import base64
import os
import random
import re
import string
import time
import urllib.parse

from qlutils import lines, new_session, parse_cookie, run_accounts, safe_json

API = "https://api.xiaoheihe.cn"
DEFAULT_HKEY_API = "https://hkey.qcciii.com/hkey"

APP_UA = (
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/41.0.2272.118 Safari/537.36 ApiMaxJia/1.0"
)

# App 请求固定的查询参数（设备相关字段单独填）
APP_PROFILE = {
    "os_type": "Android",
    "x_os_type": "Android",
    "x_client_type": "mobile",
    "x_app": "heybox",
    "time_zone": "Asia/Shanghai",
}


def env_str(name: str, default: str = "") -> str:
    return os.getenv(name, "").strip() or default


def heybox_id_from_pkey(pkey: str) -> str:
    """pkey 是 base64 编码的字符串，里面有 "_<用户ID>"，解码提取 heybox_id。"""
    encoded = urllib.parse.unquote(pkey)
    compact = re.sub(r"_+$", "", encoded) or encoded
    if not re.fullmatch(r"[A-Za-z0-9+/_-]+={0,2}", compact):
        return ""
    if len(compact.rstrip("=")) % 4 == 1:
        return ""
    padded = compact + "=" * ((4 - len(compact) % 4) % 4)
    try:
        plain = base64.b64decode(padded.replace("-", "+").replace("_", "/")).decode("utf-8", "ignore")
    except ValueError:
        return ""
    match = re.search(r"_(\d{5,})", plain)
    return match.group(1) if match else ""


class XiaoHeiHe:
    def __init__(self, cookie: str, hkey_api: str):
        fields = parse_cookie(cookie)
        pkey = fields.get("pkey", "")
        token_id = fields.get("x_xhh_tokenid", "")
        missing = [k for k, v in {
            "pkey": pkey, "x_xhh_tokenid": token_id, "imei": fields.get("imei", ""),
            "device_info": fields.get("device_info", ""), "os_version": fields.get("os_version", ""),
            "dw": fields.get("dw", ""),
        }.items() if not v]
        if missing:
            raise ValueError(f"Cookie 里缺少 {'、'.join(missing)}（格式见脚本开头的说明）")

        self.heybox_id = heybox_id_from_pkey(pkey)
        if not self.heybox_id:
            raise ValueError("无法从 pkey 解析出 heybox_id，检查 pkey 是否复制完整")

        self.hkey_api = hkey_api
        self.profile = dict(APP_PROFILE)
        self.profile.update({
            "imei": fields["imei"],
            "device_info": fields["device_info"],
            "os_version": fields["os_version"],
            "dw": fields["dw"],
        })
        if fields.get("channel"):
            self.profile["channel"] = fields["channel"]

        self.s = new_session({"User-Agent": APP_UA, "Referer": "http://api.maxjia.com/", "Accept": "application/json"})
        self.s.cookies.update({"pkey": pkey, "x_xhh_tokenid": token_id})
        self.version = ""
        self.build = ""

    # ---- App 签名 ----
    def request_hkey(self, path: str, time_sec: int) -> dict:
        """找 hkey 服务要 App 签名。返回 {hkey, _rnd, version, build, time}。"""
        params = {
            "mode": "request",
            "path": path,
            "time": time_sec,
            "imei": self.profile["imei"],
            "heybox_id": self.heybox_id,
            "os_version": self.profile["os_version"],
        }
        r = safe_json(self.s.get(self.hkey_api, params=params, timeout=15))
        if r.get("status") != "ok":
            raise RuntimeError(f"hkey 服务请求失败：{r.get('msg') or r}")
        result = r.get("result") or {}
        hkey = str(result.get("hkey") or "")
        rnd = str(result.get("_rnd") or "")
        if not re.fullmatch(r"[0-9A-F]{1,8}", hkey) or not re.fullmatch(r"\d+:[0-9A-F]{1,8}", rnd):
            raise RuntimeError(f"hkey 服务返回异常：{result}")
        self.version = str(result.get("version") or "")
        self.build = str(result.get("build") or "")
        return result

    def build_query(self, path: str, extra: dict | None = None) -> dict:
        time_sec = int(time.time())
        hk = self.request_hkey(path, time_sec)
        query = {
            **(extra or {}),
            **self.profile,
            "heybox_id": self.heybox_id,
            "nonce": "".join(random.choices(string.ascii_letters + string.digits, k=32)),
            "hkey": hk.get("hkey"),
            "_rnd": hk.get("_rnd"),
            "version": self.version,
            "build": self.build,
            "_time": str(hk.get("time") or time_sec),
        }
        return {k: v for k, v in query.items() if v}

    def get(self, path: str, extra: dict | None = None) -> dict:
        query = self.build_query(path, extra)
        return safe_json(self.s.get(API + path, params=query, timeout=15))

    # ---- 接口 ----
    def task_list(self) -> dict:
        """任务列表：返回 {username, coin, tasks: [(title, state, award)]}。"""
        r = self.get("/task/list_v2/")
        if r.get("status") != "ok":
            raise RuntimeError(f"任务列表请求失败：{r.get('msg') or r}")
        result = r.get("result") or {}
        user = result.get("user") or {}
        tasks = []
        for group in result.get("task_list") or []:
            for item in group.get("tasks") or []:
                awards = []
                for award in item.get("award_desc_v2") or []:
                    icon = str(award.get("icon") or "")
                    desc = str(award.get("desc") or "")
                    if "b9aca51c" in icon:
                        desc += "经验"
                    elif "c10d89ae" in icon:
                        desc += "H币"
                    elif "e63b192a" in icon:
                        desc += "盒电"
                    if desc:
                        awards.append(desc)
                tasks.append({
                    "title": str(item.get("title") or ""),
                    "type": str(item.get("type") or ""),
                    "state": str(item.get("state") or ""),
                    "award": " ".join(awards),
                })
        return {
            "username": str(user.get("username") or ""),
            "coin": (user.get("level_info") or {}).get("coin"),
            "tasks": tasks,
        }

    def sign(self) -> str:
        """签到，返回结果文本。"""
        r = self.get("/task/sign_v3/sign")
        if r.get("status") != "ok":
            return f"失败（{r.get('msg') or r}）"
        if str((r.get("result") or {}).get("state")) == "ignore":
            return "今天已签到"

        time.sleep(1)
        r = self.get("/task/sign_v3/get_sign_state")
        if r.get("status") != "ok":
            return f"失败（{r.get('msg') or r}）"
        state = str((r.get("result") or {}).get("state") or "")
        if state not in ("ok", "ignore"):
            return f"失败（{r.get('msg') or state or '返回异常'}）"
        result = r.get("result") or {}
        parts = []
        if result.get("sign_in_coin"):
            parts.append(f"+{result['sign_in_coin']}H币")
        if result.get("sign_in_exp"):
            parts.append(f"+{result['sign_in_exp']}经验")
        if result.get("sign_in_streak"):
            parts.append(f"连签{result['sign_in_streak']}天")
        return "成功，" + "，".join(parts) if parts else "成功"


def handle(cookie: str, _index: int) -> str:
    xhh = XiaoHeiHe(cookie, env_str("XHH_HKEY_API", DEFAULT_HKEY_API))

    snapshot = xhh.task_list()

    result: list[tuple[str, object]] = [("账号", snapshot["username"] or f"ID {xhh.heybox_id}")]
    result.append(("签到", xhh.sign()))

    # 签到完再看一眼任务和 H 币
    time.sleep(1)
    after = xhh.task_list()
    finished = sum(1 for t in after["tasks"] if t["state"] == "finish")
    unfinished = [t["title"] for t in after["tasks"] if t["state"] != "finish"]
    result.append(("每日任务", f"完成 {finished}/{len(after['tasks'])}"))
    if unfinished:
        result.append(("未完成", "、".join(unfinished[:5]) + ("…" if len(unfinished) > 5 else "")))
    result.append(("H币", after["coin"]))
    return lines(result)


if __name__ == "__main__":
    run_accounts("小黑盒签到", "XHH_COOKIE", handle)
