#!/usr/bin/env python3
"""
cron: 45 8 * * *
new Env('小黑盒签到')

小黑盒每日签到：走 /task/sign_v3/sign，顺带做分享类任务，汇报任务完成情况和 H 币余额。

两条通道，脚本自动选：
  App 通道：签到和分享任务唯一可用的通道，Cookie 要带 x_xhh_tokenid + 设备字段，
           签名和加密上报都靠外部 hkey 服务。
  Web 通道：本地算签名、不需要第三方服务，但只能读任务列表，签到会被拒（小黑盒签到只认 App 签名）。

环境变量：
    XHH_COOKIE       两种写法都行：
                     App 登录态：pkey=xxx;x_xhh_tokenid=xxx;imei=xxx;device_info=xxx;os_version=16;dw=411
                     Web 登录态：pkey=xxx（tools/xhh_cookie.py 扫码生成，只能读）
                     heybox_id 从 pkey 自动解析，不用填。多个账号用换行或 "&&" 分隔。
    XHH_HKEY_API     计算 App 签名（hkey/_rnd）和加密上报的服务地址，默认
                     https://hkey.qcciii.com/hkey。第三方服务，只会收到 heybox_id、imei、
                     接口路径、时间戳和上报内容，收不到 Cookie；服务挂了 App 通道就不可用。
    XHH_AUTO_TASKS   默认 true。设为 false 只签到，不做浏览/分享任务（兼容旧名 XHH_SHARE_TASKS）。

自动任务（App 通道）：
    前往首页浏览三篇帖子（每篇上报 30 秒）、分享任意帖子 / 游戏详情 / 游戏评价 —— 模拟浏览后加密上报。
    「参与主题创作活动」需要真的发内容，脚本不做。

接口参考 yowiv08/heybox（GitHub）的 heybox_sign.js；Web 签名算法来自公开的逆向分析。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import random
import re
import string
import time
import urllib.parse
import uuid

from qlutils import lines, new_session, parse_cookie, run_accounts, safe_json

API = "https://api.xiaoheihe.cn"
DATA_API = "https://data.xiaoheihe.cn"
DEFAULT_HKEY_API = "https://hkey.qcciii.com/hkey"

# 分享任务用的接口参数（照 App 的请求抄的）
FEEDS_QUERY = {
    "pull": "1", "is_first": "1", "list_ver": "2", "use_waterfall_style": "2", "has_cache": "0",
    "media_volume_percent": "6", "audio_output_type": "speaker",
    "screen_brightness_percent": "68", "thermal_state": "nominal",
    "is_charging": "0", "netmode": "wifi",
}
GAME_RECOMMEND_QUERY = {"offset": "0", "limit": "1"}
GAME_COMMENTS_QUERY = {"api_version": "4", "offset": "0", "limit": "30"}

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
        """签到。状态接口有时滞后，用任务列表兜底判断是否真的完成。"""
        r = self.get("/task/sign_v3/sign")
        if r.get("status") != "ok":
            raise SignError(str(r.get("msg") or r))
        if str((r.get("result") or {}).get("state")) == "ignore":
            return "今天已签到"

        last = "状态未更新"
        for _ in range(4):
            time.sleep(1.5)
            state_r = self.get("/task/sign_v3/get_sign_state")
            result = state_r.get("result") or {}
            state = str(result.get("state") or "")
            if state == "ignore":
                return "今天已签到"
            if state == "ok":
                return self._sign_summary(result)

            # 状态接口还在 waiting 时，看任务列表里的签到任务是不是已经 finish
            try:
                snapshot = self.task_list()
            except Exception:  # noqa: BLE001
                snapshot = None
            task = next((t for t in (snapshot or {}).get("tasks", []) if t["type"] == "sign"), None)
            if task and task["state"] == "finish":
                return "成功（已签到，奖励明细稍后到账）"
            last = str(state_r.get("msg") or state or "返回异常")
        raise SignError(f"状态一直停在 {last}")

    @staticmethod
    def _sign_summary(result: dict) -> str:
        parts = []
        if result.get("sign_in_coin"):
            parts.append(f"+{result['sign_in_coin']}H币")
        if result.get("sign_in_exp"):
            parts.append(f"+{result['sign_in_exp']}经验")
        if result.get("sign_in_streak"):
            parts.append(f"连签{result['sign_in_streak']}天")
        return "成功，" + "，".join(parts) if parts else "成功"

    # ---- 分享任务（只有 App 通道能做：需要 report 模式加密上报）----
    def report_hkey(self, path: str, text: str, time_sec: str) -> dict:
        """让签名服务把上报内容加密，返回 {hkey,_rnd,data,key,sid,...}。"""
        r = safe_json(self.s.post(self.hkey_api, json={
            "mode": "report",
            "text": text,
            "path": path,
            "time": time_sec,
            "imei": self.profile.get("imei", ""),
            "heybox_id": self.heybox_id,
            "os_version": self.profile.get("os_version", ""),
        }, timeout=20))
        if r.get("status") != "ok":
            raise RuntimeError(f"签名服务（report）失败：{r.get('msg') or r}")
        return r.get("result") or {}

    def post_encrypted(self, path: str, text: str, extra: dict | None = None) -> dict:
        time_sec = str(int(time.time()))
        hk = self.report_hkey(path, text, time_sec)
        query = {
            **self.profile,
            "heybox_id": self.heybox_id,
            "nonce": "".join(random.choices(string.ascii_letters + string.digits, k=32)),
            "hkey": hk.get("hkey"),
            "_rnd": hk.get("_rnd"),
            "version": hk.get("version") or self.version,
            "build": hk.get("build") or self.build,
            "_time": str(hk.get("time") or time_sec),
            "time_": time_sec,
            **(extra or {}),
        }
        query = {k: v for k, v in query.items() if v}
        body = {"data": hk.get("data"), "key": hk.get("key"), "sid": hk.get("sid")}
        return safe_json(self.s.post(DATA_API + path, params=query, data=body, timeout=20))

    def send_share_events(self, source: str, extra: dict) -> None:
        """上报「点了分享」和「分享成功」两个事件，中间隔 2 秒，像真人操作。"""
        session_id = str(uuid.uuid4())
        for action, event_type in (("tap", "4"), ("success", "3")):
            text = json.dumps({"events": [{
                "type": event_type,
                "path": f"/share/behavior/{action}",
                "time": str(int(time.time())),
                "addition": {**extra, "src": source, "plat": "WechatSession"},
            }]}, ensure_ascii=False, separators=(",", ":"))
            r = self.post_encrypted("/account/data_report/", text,
                                    {"type": "104", "session_id": session_id})
            if r.get("status") != "ok":
                raise RuntimeError(f"{action} 上报失败：{r.get('msg') or r.get('status')}")
            if action == "tap":
                time.sleep(2)

    def feed_posts(self) -> list[dict]:
        """拉首页信息流，返回去重后的 [{link_id, h_src}]。"""
        feed = self.get("/bbs/app/feeds", FEEDS_QUERY)
        if feed.get("status") != "ok":
            raise RuntimeError(f"拉取帖子流失败：{feed.get('msg') or feed.get('status')}")
        posts: list[dict] = []
        seen: set[str] = set()
        for item in (feed.get("result") or {}).get("links") or []:
            link_id = str(item.get("link_id") or "")
            h_src = str(item.get("h_src") or "")
            if not re.fullmatch(r"[1-9]\d*", link_id) or not h_src or link_id in seen:
                continue
            seen.add(link_id)
            posts.append({"link_id": link_id, "h_src": h_src})
        return posts

    def report_view(self, post: dict, seconds: int) -> None:
        """上报一篇帖子的阅读时长。"""
        text = json.dumps({
            "duration": [{
                "id": int(post["link_id"]),
                "duration": seconds,
                "duration_ms": seconds * 1000,
                "type": "link",
                "time": int(time.time()),
                "h_src": post["h_src"],
            }],
            "shows": [],
            "disappear": [],
        }, ensure_ascii=False, separators=(",", ":"))
        r = self.post_encrypted("/bbs/app/link/view/time", text)
        if r.get("status") != "ok":
            raise RuntimeError(f"浏览时长上报失败：{r.get('msg') or r.get('status')}")

    def browse_posts(self, count: int = 3, seconds: int = 30) -> None:
        """浏览三篇帖子，每篇上报 30 秒阅读时长。"""
        posts = self.feed_posts()
        if len(posts) < count:
            raise RuntimeError(f"信息流只拿到 {len(posts)} 篇帖子，不够 {count} 篇")
        for post in posts[:count]:
            time.sleep(1)
            self.report_view(post, seconds)
            time.sleep(2)

    def share_post(self) -> None:
        """分享任意帖子：拉信息流 → 上报浏览时长 → 上报分享事件。"""
        posts = self.feed_posts()
        if not posts:
            raise RuntimeError("信息流里没有可用帖子")
        post = posts[0]
        time.sleep(1)
        self.report_view(post, 5)
        self.send_share_events("link", {"link_id": post["link_id"], "h_src": post["h_src"]})

    def recommend_game(self) -> dict:
        r = self.get("/game/all_recommend/v2", GAME_RECOMMEND_QUERY)
        if r.get("status") != "ok":
            raise RuntimeError(f"拉取游戏列表失败：{r.get('msg') or r.get('status')}")
        for section in (r.get("result") or {}).get("all_list") or []:
            items = []
            if section.get("type") == "game_card_duo":
                items = section.get("games") or []
            elif section.get("type") == "middle_game_card_video":
                items = [section.get("game") or {}]
            for game in items:
                appid = str(game.get("appid") or "")
                h_src = str(game.get("h_src") or "")
                if re.fullmatch(r"[1-9]\d*", appid) and h_src:
                    return {"appid": appid, "h_src": h_src}
        raise RuntimeError("推荐列表里没有可用游戏")

    def share_game_detail(self) -> None:
        game = self.recommend_game()
        time.sleep(1)
        self.send_share_events("game_detail", {"appid": game["appid"]})

    def share_game_comment(self) -> None:
        game = self.recommend_game()
        r = self.get("/bbs/app/link/game/comments", {**GAME_COMMENTS_QUERY, "appid": game["appid"]})
        if r.get("status") != "ok":
            raise RuntimeError(f"拉取游戏评论失败：{r.get('msg') or r.get('status')}")
        comment = None
        for item in (r.get("result") or {}).get("links") or []:
            link_id = str(item.get("linkid") or "")
            user_id = str(item.get("userid") or "")
            h_src = str(item.get("h_src") or "")
            if re.fullmatch(r"[1-9]\d*", link_id) and re.fullmatch(r"[1-9]\d*", user_id) and h_src:
                comment = {"link_id": link_id, "h_src": h_src}
                break
        if not comment:
            raise RuntimeError("评论列表里没有可用条目")
        self.send_share_events("game_comment", {"link_id": comment["link_id"]})


def sign_with_fallback(cookie: str, hkey_api: str, account: XiaoHeiHe) -> str:
    """签到一次。网页登录态在 App 通道不可用，失败时直接给出可读原因。"""
    try:
        return account.sign()
    except SignError as error:
        message = str(error)
        if account.channel == "web" and "非法" in message:
            return f"失败（{message}，网页登录态签不了；用 tools/xhh_login.py 换 App 登录态）"
        return f"失败（{message}）"


def task_kind(title: str) -> str:
    """按任务标题判断是哪一种能自动做的任务，认不出来就返回空。"""
    if "浏览" in title and "帖子" in title:
        return "browse"
    if "分享" in title:
        if "帖子" in title:
            return "post"
        if "游戏详情" in title:
            return "game_detail"
        if "评价" in title:
            return "game_comment"
    return ""


def run_auto_tasks(account: XiaoHeiHe, tasks: list[dict]) -> tuple[list[str], list[str]]:
    """把未完成的浏览/分享任务逐个做掉，返回（成功标题, 失败说明）。"""
    handlers = {
        "browse": account.browse_posts,
        "post": account.share_post,
        "game_detail": account.share_game_detail,
        "game_comment": account.share_game_comment,
    }
    done: list[str] = []
    failed: list[str] = []
    for task in tasks:
        if task["state"] == "finish":
            continue
        kind = task_kind(task["title"])
        if not kind:
            continue
        try:
            handlers[kind]()
            time.sleep(2)
            snapshot = account.task_list()
            current = next((t for t in snapshot["tasks"] if t["title"] == task["title"]), None)
            if current and current["state"] == "finish":
                done.append(task["title"])
            else:
                failed.append(f"{task['title']}（已上报但状态未变）")
        except Exception as error:  # noqa: BLE001
            failed.append(f"{task['title']}（{error}）")
    return done, failed


def env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value not in ("0", "false", "no", "off")


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

    # 浏览/分享任务只有 App 通道能做（要 report 模式的加密上报）
    auto_enabled = env_flag("XHH_AUTO_TASKS", env_flag("XHH_SHARE_TASKS", True))
    if xhh.channel == "app" and auto_enabled:
        done, failed = run_auto_tasks(xhh, after["tasks"])
        if done or failed:
            summary = f"{len(done)}/{len(done) + len(failed)}"
            detail = "、".join(failed[:2]) + ("…" if len(failed) > 2 else "")
            result.append(("自动任务", summary + (f"，失败：{detail}" if failed else "")))
            if done:
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
