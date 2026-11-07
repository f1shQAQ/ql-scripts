#!/usr/bin/env python3
"""
cron: 30 8 * * *
new Env('B站每日任务')

B站每日任务：漫画签到、直播签到、观看视频、分享视频，可选投币、银瓜子换硬币、领取大会员权益。

环境变量：
    BILI_COOKIE        bilibili.com 的完整 Cookie（必须有 SESSDATA 和 bili_jct）。
                       多个账号用换行或 "&&" 分隔。
    BILI_COIN_NUM      每天投币的总数，0～5，默认 0（不投币，硬币不会被花掉）。
    BILI_SILVER2COIN   "true" 时用银瓜子换 1 个硬币，默认 false。
    BILI_VIP_PRIVILEGE "false" 时不领取大会员权益，默认 true。

接口参考 Sitoi/dailycheckin 的 bilibili 模块（MIT）和 SocialSisterYi/bilibili-API-collect。
"""

from __future__ import annotations

import time

from qlutils import env_bool, env_int, lines, new_session, parse_cookie, run_accounts, safe_json

API = "https://api.bilibili.com"
LIVE = "https://api.live.bilibili.com"
MANGA = "https://manga.bilibili.com"


class Bili:
    def __init__(self, cookie: str):
        self.cookies = parse_cookie(cookie)
        self.csrf = self.cookies.get("bili_jct", "")
        self.s = new_session({"Referer": "https://www.bilibili.com/", "Origin": "https://www.bilibili.com"})
        self.s.cookies.update(self.cookies)

    def get(self, url: str, **kw) -> dict:
        return safe_json(self.s.get(url, timeout=15, **kw))

    def post(self, url: str, **kw) -> dict:
        return safe_json(self.s.post(url, timeout=15, **kw))

    # ---- 账号信息 ----
    def nav(self) -> dict:
        return self.get(f"{API}/x/web-interface/nav").get("data") or {}

    def today_exp_log(self) -> list[dict]:
        today = time.strftime("%Y-%m-%d")
        data = self.get(f"{API}/x/member/web/exp/log", params={"jsonp": "jsonp"}).get("data") or {}
        return [x for x in data.get("list") or [] if str(x.get("time", "")).startswith(today)]

    # ---- 签到 ----
    def manga_sign(self) -> str:
        r = self.post(f"{MANGA}/twirp/activity.v1.Activity/ClockIn", data={"platform": "android"})
        if r.get("code") == 0:
            return "成功"
        if "duplicate" in str(r.get("msg", "")):
            return "今天已签到"
        return f"失败（{r.get('msg') or r}）"

    def live_sign(self) -> str:
        r = self.get(f"{LIVE}/xlive/web-ucenter/v1/sign/DoSign")
        if r.get("code") == 0:
            d = r.get("data") or {}
            return f"成功，{d.get('text', '')}，本月已签 {d.get('hadSignDays', '?')} 天"
        if r.get("code") == 1011040:
            return "今天已签到"
        return f"失败（{r.get('message') or r}）"

    # ---- 视频任务 ----
    def videos(self) -> list[dict]:
        data = self.get(f"{API}/x/web-interface/popular", params={"ps": 20, "pn": 1}).get("data") or {}
        items = data.get("list") or []
        if not items:
            data = self.get(f"{API}/x/web-interface/dynamic/region", params={"ps": 12, "rid": 1}).get("data") or {}
            items = data.get("archives") or []
        return [
            {"aid": v.get("aid"), "cid": v.get("cid"), "title": v.get("title", ""), "up_mid": (v.get("owner") or {}).get("mid")}
            for v in items
            if v.get("aid")
        ]

    def watch(self, v: dict) -> str:
        r = self.post(
            f"{API}/x/v2/history/report",
            data={"aid": v["aid"], "cid": v["cid"], "progress": 300, "platform": "web", "csrf": self.csrf},
        )
        return f"《{v['title']}》300 秒" if r.get("code") == 0 else f"失败（{r.get('message') or r}）"

    def share(self, v: dict) -> str:
        r = self.post(
            f"{API}/x/web-interface/share/add",
            data={"aid": v["aid"], "eab_x": 2, "ramval": 0, "source": "web_normal", "ga": 1, "csrf": self.csrf},
        )
        if r.get("code") in (0, 71000):  # 71000：今天已分享过这个视频
            return f"《{v['title']}》"
        return f"失败（{r.get('message') or r}）"

    def add_coins(self, target: int, coins_left: float, videos: list[dict], my_mid) -> str:
        done_today = sum(1 for x in self.today_exp_log() if "投币" in str(x.get("reason", "")))
        need = min(max(target - done_today, 0), int(coins_left))
        added = 0
        for v in videos:
            if added >= need:
                break
            if v.get("up_mid") == my_mid:
                continue
            r = self.post(
                f"{API}/x/web-interface/coin/add",
                data={"aid": v["aid"], "multiply": 1, "select_like": 1, "cross_domain": "true", "csrf": self.csrf},
            )
            code = r.get("code")
            if code == 0:
                added += 1
                time.sleep(2)
            elif code == 34005:  # 这个视频已投满
                continue
            else:  # -104 硬币不足，-111 csrf 校验失败，其他错误都停止
                print(f"投币停止：{r.get('message') or r}")
                break
        return f"今天已投 {done_today + added}/{target}（本次 {added}）"

    # ---- 可选任务 ----
    def silver2coin(self) -> str:
        r = self.post(f"{LIVE}/xlive/revenue/v1/wallet/silver2coin", data={"csrf": self.csrf})
        return "成功" if r.get("code") == 0 else str(r.get("message") or r)

    def vip_privilege(self, vip_type: int) -> str:
        data = self.get(f"{API}/x/vip/privilege/my").get("data") or {}
        got = 0
        for item in data.get("list") or []:
            if item.get("state") == 0 and item.get("vip_type") == vip_type:
                r = self.post(f"{API}/x/vip/privilege/receive", data={"type": item.get("type"), "csrf": self.csrf})
                if r.get("code") == 0:
                    got += 1
        return f"领取 {got} 项" if got else "没有可领取的权益"


def handle(cookie: str, _index: int) -> str:
    bili = Bili(cookie)
    if not bili.cookies.get("SESSDATA") or not bili.csrf:
        raise ValueError("Cookie 里缺少 SESSDATA 或 bili_jct")

    info = bili.nav()
    if not info.get("isLogin"):
        raise ValueError("Cookie 已失效，请重新获取")

    result: list[tuple[str, object]] = [("账号", info.get("uname"))]
    result.append(("漫画签到", bili.manga_sign()))
    result.append(("直播签到", bili.live_sign()))

    videos = bili.videos()
    if videos:
        result.append(("观看视频", bili.watch(videos[0])))
        result.append(("分享视频", bili.share(videos[0])))
        coin_num = min(max(env_int("BILI_COIN_NUM", 0), 0), 5)
        if coin_num:
            result.append(("投币", bili.add_coins(coin_num, info.get("money") or 0, videos, info.get("mid"))))
    else:
        result.append(("视频任务", "没有拿到视频列表，跳过"))

    if env_bool("BILI_SILVER2COIN"):
        result.append(("银瓜子换硬币", bili.silver2coin()))
    vip_type = info.get("vipType") or 0
    if vip_type and (info.get("vipStatus") == 1) and env_bool("BILI_VIP_PRIVILEGE", True):
        result.append(("大会员权益", bili.vip_privilege(vip_type)))

    after = bili.nav()
    level = after.get("level_info") or {}
    today_exp = sum(int(x.get("delta") or 0) for x in bili.today_exp_log())
    result += [
        ("等级", f"Lv{level.get('current_level', '?')}"),
        ("经验", f"{level.get('current_exp', '?')}/{level.get('next_exp', '?')}"),
        ("今日经验", f"+{today_exp}"),
        ("硬币", after.get("money")),
    ]
    return lines(result)


if __name__ == "__main__":
    run_accounts("B站每日任务", "BILI_COOKIE", handle)
