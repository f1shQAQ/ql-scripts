#!/usr/bin/env python3
"""
cron: 45 8 * * *
new Env('小黑盒签到')

小黑盒每日签到：走 /task/sign_v3/sign，顺便汇报每日任务完成情况和 H 币余额。

两条通道，脚本自动选：
  Web 通道（默认，推荐）：本地算签名，不需要第三方服务，Cookie 只要 pkey。
  App 通道：需要抓包拿到的 Cookie + 设备字段，签名得靠外部 hkey 服务（XHH_HKEY_API）。

环境变量：
    XHH_COOKIE     两种写法都行：
                    Web 登录态：pkey=xxx（tools/xhh_cookie.py 扫码生成的就是这种）
                    App 抓包： pkey=xxx;x_xhh_tokenid=xxx;imei=xxx;device_info=xxx;os_version=16;dw=411
                    heybox_id 从 pkey 自动解析，不用填。多个账号用换行或 "&&" 分隔。
    XHH_HKEY_API    只在 App 通道用：计算 App 签名（hkey/_rnd）的服务地址，默认
                    https://hkey.qcciii.com/hkey。第三方服务，只会收到 heybox_id、imei、
                    接口路径和时间戳，收不到 Cookie；服务挂了 App 通道就不可用。

接口参考 yowiv08/heybox（GitHub）的 heybox_sign.js；Web 签名算法来自公开的逆向分析。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
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
WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
WEB_ORIGIN = "https://xiaoheihe.cn"

# App 请求固定的查询参数（设备相关字段单独填）
APP_PROFILE = {
    "os_type": "Android",
    "x_os_type": "Android",
    "x_client_type": "mobile",
    "x_app": "heybox",
    "time_zone": "Asia/Shanghai",
}

# Web 请求固定的查询参数（x_client_type=weboutapp 是网页版任务中心用的那套，实测能过签名校验）
WEB_PROFILE = {
    "os_type": "web",
    "app": "heybox",
    "x_client_type": "weboutapp",
    "x_client_version": "",
    "x_os_type": "Windows",
    "x_app": "heybox",
}

WEB_CHARSET = "AB45STUVWZEFGJ6CH01D237IXYPQRKLMN89"
WEB_RND_KEY = "Z7mFG4tQp9Ws2LxB8H"


# ---- Web 通道签名（纯本地，无外部依赖）----
def _map_all(text: str, pool: str) -> str:
    return "".join(pool[ord(char) % len(pool)] for char in text)


def _map_head(text: str, pool: str, cut: int) -> str:
    sub = pool[:cut]
    return "".join(sub[ord(char) % len(sub)] for char in text)


def _interleave(parts: list[str]) -> str:
    longest = max(len(part) for part in parts)
    return "".join(part[i] for i in range(longest) for part in parts if i < len(part))


def _double(value: int) -> int:
    return ((value << 1) ^ 27) & 255 if value & 128 else value << 1


def _add(value: int) -> int:
    return _double(value) ^ value


def _spread(value: int) -> int:
    return _add(_double(value))


def _fold(value: int) -> int:
    return _spread(_add(_double(value)))


def _shift(value: int) -> int:
    return _fold(value) ^ _spread(value) ^ _add(value)


def _mix_column(values: list[int]) -> list[int]:
    a, b, c, d = values[:4]
    values[:4] = [
        _shift(a) ^ _fold(b) ^ _spread(c) ^ _add(d),
        _add(a) ^ _shift(b) ^ _fold(c) ^ _spread(d),
        _spread(a) ^ _add(b) ^ _shift(c) ^ _fold(d),
        _fold(a) ^ _spread(b) ^ _add(c) ^ _shift(d),
    ]
    return values


def web_hkey(path: str, timestamp: int, nonce: str) -> str:
    """Web 通道的 hkey：路径 + 时间 + nonce 做两轮字符映射后取 MD5。"""
    normalized = "/" + "/".join(part for part in path.split("/") if part) + "/"
    mixed = _interleave([
        _map_head(str(timestamp + 1), WEB_CHARSET, -2),
        _map_all(normalized, WEB_CHARSET),
        _map_all(nonce, WEB_CHARSET),
    ])[:20]
    digest = hashlib.md5(mixed.encode()).hexdigest()
    values = _mix_column([ord(char) for char in digest[-6:]])
    return _map_head(digest[:5], WEB_CHARSET, -4) + f"{sum(values) % 100:02d}"


def web_rnd(nonce: str, timestamp: int) -> str:
    message = (WEB_RND_KEY + nonce + f"{timestamp}:{nonce}").encode()
    return "15:" + hmac.new(WEB_RND_KEY.encode(), message, hashlib.sha256).hexdigest()


def web_sign(path: str) -> dict[str, str]:
    now = int(time.time())
    nonce = "".join(random.choices(string.ascii_letters + string.digits, k=32))
    return {
        "_time": str(now),
        "nonce": nonce,
        "hkey": web_hkey(path, now, nonce),
        "_rnd": web_rnd(nonce, now),
        "version": "999.0.4",
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


class SignError(RuntimeError):
    """签到接口返回失败。"""


def default_device(pkey: str) -> dict[str, str]:
    """Cookie 里没带设备字段时按 pkey 生成一套固定值（同一账号每次一致）。"""
    digest = hashlib.sha1(pkey.encode()).hexdigest()
    return {
        "imei": "86" + "".join(str(int(char, 16) % 10) for char in digest[:13]),
        "device_info": "M2012K11AC",
        "os_version": "13",
        "dw": "411",
    }


class XiaoHeiHe:
    def __init__(self, cookie: str, hkey_api: str, channel: str = "auto"):
        fields = parse_cookie(cookie)
        pkey = fields.get("pkey") or fields.get("user_pkey", "")
        token_id = fields.get("x_xhh_tokenid", "")
        device = {key: fields.get(key, "") for key in ("imei", "device_info", "os_version", "dw")}
        if not pkey:
            raise ValueError("Cookie 里缺少 pkey（扫码工具生成的或抓包拿到的登录凭据）")

        self.heybox_id = heybox_id_from_pkey(pkey)
        if not self.heybox_id:
            raise ValueError("无法从 pkey 解析出 heybox_id，检查 pkey 是否复制完整")

        # 有完整设备字段和 x_xhh_tokenid 的是抓包来的 App 登录态，先走 App 通道；
        # 其余（网页扫码拿到的）直接走 Web 通道，不需要设备字段，也不需要外部签名服务。
        if channel == "auto":
            channel = "app" if (token_id and all(device.values())) else "web"
        if channel == "app":
            fallback = default_device(pkey)
            device = {key: value or fallback[key] for key, value in device.items()}

        self.channel = channel
        self.hkey_api = hkey_api
        self.version = ""
        self.build = ""

        if channel == "app":
            self.profile = {**APP_PROFILE, **device}
            if fields.get("channel"):
                self.profile["channel"] = fields["channel"]
            self.s = new_session({
                "User-Agent": APP_UA,
                "Referer": "http://api.maxjia.com/",
                "Accept": "application/json",
            })
        else:
            self.profile = dict(WEB_PROFILE)
            self.s = new_session({
                "User-Agent": WEB_UA,
                "Referer": f"{WEB_ORIGIN}/",
                "Origin": WEB_ORIGIN,
                "Accept": "application/json, text/plain, */*",
            })

        if channel == "app":
            self.s.cookies.update({"pkey": pkey})
        else:
            # 网页端四种写法都会带，服务端读哪个都能命中
            self.s.cookies.update({
                "pkey": pkey,
                "user_pkey": pkey,
                "heybox_id": self.heybox_id,
                "user_heybox_id": self.heybox_id,
            })
        if token_id:
            self.s.cookies.update({"x_xhh_tokenid": token_id})

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
        if self.channel == "web":
            query = {
                **self.profile,
                "heybox_id": self.heybox_id,
                **web_sign(path),
                **(extra or {}),
            }
            return {k: v for k, v in query.items() if v}

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
            raise SignError(str(r.get("msg") or r))
        if str((r.get("result") or {}).get("state")) == "ignore":
            return "今天已签到"

        time.sleep(1)
        r = self.get("/task/sign_v3/get_sign_state")
        if r.get("status") != "ok":
            raise SignError(str(r.get("msg") or r))
        state = str((r.get("result") or {}).get("state") or "")
        if state not in ("ok", "ignore"):
            raise SignError(str(r.get("msg") or state or "返回异常"))
        result = r.get("result") or {}
        parts = []
        if result.get("sign_in_coin"):
            parts.append(f"+{result['sign_in_coin']}H币")
        if result.get("sign_in_exp"):
            parts.append(f"+{result['sign_in_exp']}经验")
        if result.get("sign_in_streak"):
            parts.append(f"连签{result['sign_in_streak']}天")
        return "成功，" + "，".join(parts) if parts else "成功"


def sign_with_fallback(cookie: str, hkey_api: str, account: XiaoHeiHe) -> str:
    """签到一次。网页登录态在 App 通道不可用，失败时直接给出可读原因。"""
    try:
        return account.sign()
    except SignError as error:
        return f"失败（{error}）"


def open_account(cookie: str, hkey_api: str) -> tuple[XiaoHeiHe, dict]:
    """按 Cookie 形态挑通道，探测失败自动换另一条。"""
    fields = parse_cookie(cookie)
    has_device = all(fields.get(key) for key in ("imei", "device_info", "os_version", "dw"))
    prefer_app = bool(fields.get("x_xhh_tokenid")) and has_device

    problems: list[str] = []
    for channel in (["app", "web"] if prefer_app else ["web", "app"]):
        try:
            account = XiaoHeiHe(cookie, hkey_api, channel=channel)
            snapshot = account.task_list()
        except Exception as error:  # noqa: BLE001
            problems.append(f"{channel} 通道：{error}")
            continue
        return account, snapshot
    raise RuntimeError("；".join(problems) or "登录失败，检查 Cookie 是否有效")


def handle(cookie: str, _index: int) -> str:
    hkey_api = env_str("XHH_HKEY_API", DEFAULT_HKEY_API)
    xhh, snapshot = open_account(cookie, hkey_api)

    result: list[tuple[str, object]] = [
        ("账号", snapshot["username"] or f"ID {xhh.heybox_id}"),
        ("通道", "Web（本地签名）" if xhh.channel == "web" else "App（hkey 服务）"),
        ("签到", sign_with_fallback(cookie, hkey_api, xhh)),
    ]

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
