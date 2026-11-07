#!/usr/bin/env python3
"""
cron: 20 9 * * *
new Env('Microsoft Rewards')

Microsoft Rewards 国区每日任务（cn.bing.com）：
    电脑端搜索、手机端搜索、每日活动（Daily Set / More Activities）、
    Bing App 每日签到、阅读赚积分。

环境变量：
    MSR_COOKIE          推荐用 Cookie-Editor 插件分别在 https://rewards.bing.com 和
                        https://login.live.com/oauth20_desktop.srf 导出 JSON，两段直接拼在一起填入。
                        有 login.live.com 的 Cookie 时，rewards 会话过期后脚本会自动续登。
                        也支持 cookies.txt 和普通 Cookie 请求头（请求头很快会过期）。
                        多个账号用 "&&" 分隔。
    MSR_REFRESH_TOKEN   可选。App 签到和阅读用的 refresh token，在本地运行 tools/msr_token.py 获取。
                        多个账号按 MSR_COOKIE 的顺序排列，用换行或 "&&" 分隔。
                        脚本会把微软返回的新 token 自动写回这个变量（需要青龙 2.18+）。
    MSR_SEARCH          "false" 时不做搜索，默认 true。
    MSR_SEARCH_DELAY    两次搜索之间的间隔秒数，默认 15。

接口参考 TheNetsky/Microsoft-Rewards-Script 和 Greasy Fork 脚本 560503。
Microsoft Rewards 条款禁止自动化，账号有被封的风险。
"""

from __future__ import annotations

import html
import json
import random
import re
import time
import uuid
from datetime import datetime
from urllib.parse import quote

from qlutils import (
    env_bool,
    env_int,
    get_accounts,
    get_cookie_accounts,
    lines,
    new_session,
    parse_cookie_export,
    replace_env_value,
    run_accounts,
    safe_json,
)

BING = "https://cn.bing.com"
REWARDS = "https://rewards.bing.com"
PLATFORM = "https://prod.rewardsplatform.microsoft.com"
TOKEN_URL = "https://login.live.com/oauth20_token.srf"
CLIENT_ID = "0000000040170455"
SCOPE = "service::prod.rewardsplatform.microsoft.com::MBI_SSL"

PC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
)
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36 EdgA/130.0.0.0"
)
APP_HEADERS = {
    "X-Rewards-AppId": "SAAndroid/34.5.450113001",
    "X-Rewards-IsMobile": "true",
    "X-Rewards-Country": "cn",
    "X-Rewards-PartnerId": "startapp",
    "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) BingSapphire/34.5.450113001",
}
POINTS_PER_SEARCH = 3

FALLBACK_WORDS = [
    "今日天气", "新闻热点", "汇率查询", "高铁时刻表", "电影推荐", "家常菜做法", "健身计划",
    "Python 教程", "旅游攻略", "手机评测", "历史上的今天", "股市行情", "英语单词", "地铁线路",
    "养花技巧", "跑步配速", "咖啡种类", "睡眠质量", "读书笔记", "相机参数", "春节习俗",
    "围棋规则", "火锅底料", "自行车保养", "宠物猫品种", "网速测试", "日历", "翻译",
    "世界地图", "天文现象", "诗词鉴赏", "数学公式", "化学元素周期表", "台风路径",
]


# ---------------------------------------------------------------- 搜索词

def hot_words() -> list[str]:
    """取百度、微博热搜做搜索词，都失败时用内置词表。"""
    words: list[str] = []
    s = new_session()
    try:
        data = safe_json(s.get("https://top.baidu.com/api/board?platform=wise&tab=realtime", timeout=10))
        for card in (data.get("data") or {}).get("cards") or []:
            for item in card.get("content") or []:
                for sub in item.get("content") or [item]:
                    if sub.get("word"):
                        words.append(sub["word"])
    except Exception:  # noqa: BLE001
        pass
    try:
        data = safe_json(s.get("https://weibo.com/ajax/side/hotSearch", timeout=10))
        words += [x.get("word") for x in (data.get("data") or {}).get("realtime") or [] if x.get("word")]
    except Exception:  # noqa: BLE001
        pass
    words = list(dict.fromkeys(w.strip() for w in words if w and w.strip()))
    random.shuffle(words)
    rest = [w for w in FALLBACK_WORDS if w not in words]
    random.shuffle(rest)
    return words + rest


# ---------------------------------------------------------------- 网页端

class Web:
    def __init__(self, cookie_text: str):
        self.s = new_session()
        self.cookies = parse_cookie_export(cookie_text, ".bing.com")
        for c in self.cookies:
            self.s.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"], secure=c["secure"])
        self.has_live = any("live.com" in c["domain"] for c in self.cookies)
        domains: dict[str, int] = {}
        for c in self.cookies:
            domains[c["domain"]] = domains.get(c["domain"], 0) + 1
        print("读到的 Cookie：" + "，".join(f"{d} {n} 个" for d, n in domains.items()))
        if not any(c["name"] == "_U" for c in self.cookies):
            print("提示：没有 _U，Bing 搜索可能不计分")

    def _get_userinfo(self):
        return self.s.get(
            f"{REWARDS}/api/getuserinfo",
            params={"type": 1, "X-Requested-With": "XMLHttpRequest", "_": int(time.time() * 1000)},
            headers={"Referer": f"{REWARDS}/", "X-Requested-With": "XMLHttpRequest"},
            timeout=20,
        )

    def sso(self) -> None:
        """rewards.bing.com 的会话过期后，用 login.live.com 的登录态静默续登，和浏览器自动跳转的过程一样。"""
        r = self.s.get(f"{REWARDS}/Signin", params={"idru": "/", "silent": "True"}, timeout=20)
        for _ in range(4):
            form = re.search(r"<form[^>]*\baction=\"([^\"]+)\"[^>]*>(.*?)</form>", r.text, re.S | re.I)
            if not form:
                return
            fields: dict[str, str] = {}
            for tag in re.findall(r"<input[^>]*>", form.group(2), re.I):
                name = re.search(r'\bname="([^"]*)"', tag)
                value = re.search(r'\bvalue="([^"]*)"', tag)
                if name:
                    fields[name.group(1)] = html.unescape(value.group(1)) if value else ""
            if fields.get("error"):
                raise ValueError(
                    f"login.live.com 拒绝静默登录（{fields['error']}）。请在浏览器重新登录微软账号，"
                    "勾选「保持登录」，再按 README 重新导出 Cookie"
                )
            r = self.s.post(html.unescape(form.group(1)), data=fields, timeout=20)

    def userinfo(self) -> dict:
        resp = self._get_userinfo()
        if resp.status_code == 401:
            if not self.has_live:
                raise ValueError(
                    "rewards.bing.com 的会话已过期（HTTP 401）。只复制请求头的 Cookie 撑不了多久，"
                    "请按 README 用 Cookie-Editor 导出 rewards.bing.com 和 login.live.com 两份 JSON"
                )
            self.sso()
            resp = self._get_userinfo()
        data = safe_json(resp)
        dash = data.get("dashboard") or data
        if not isinstance(dash, dict) or not dash.get("userStatus"):
            hint = "接口可能改版了" if resp.status_code in (200, 404) else "登录态无效"
            raise ValueError(f"取不到积分面板（HTTP {resp.status_code}，{hint}）：{str(data)[:150]}")
        return dash

    @staticmethod
    def points(dash: dict) -> int:
        return int((dash.get("userStatus") or {}).get("availablePoints") or 0)

    @staticmethod
    def counter(dash: dict, name: str) -> tuple[int, int]:
        items = ((dash.get("userStatus") or {}).get("counters") or {}).get(name) or []
        progress = sum(int(x.get("pointProgress") or 0) for x in items)
        maximum = sum(int(x.get("pointProgressMax") or x.get("pointMax") or 0) for x in items)
        return progress, maximum

    # ---- 搜索 ----
    def search_once(self, word: str, mobile: bool) -> bool:
        today = datetime.now().strftime("%Y-%m-%d")
        self.s.cookies.set("_Rwho", f"u={'m' if mobile else 'd'}&ts={today}", domain=".bing.com", path="/")
        headers = {"User-Agent": MOBILE_UA if mobile else PC_UA}
        cvid = uuid.uuid4().hex.upper()
        url = f"{BING}/search?q={quote(word)}&form=QBLH&cvid={cvid}"
        page = self.s.get(url, headers=headers, timeout=20)
        m = re.search(r'IG:"([0-9A-F]+)"', page.text)
        if not m:
            return False
        ig = m.group(1)
        post_headers = {
            **headers,
            "Referer": url,
            "Origin": BING,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
        }
        self.s.post(
            f"{BING}/rewardsapp/ncheader",
            params={"ver": "88888888", "IID": "SERP.5047", "IG": ig, "ajaxreq": 1},
            data="wb=1%3bi%3d1%3bv%3d1",
            headers=post_headers,
            timeout=20,
        )
        r = self.s.post(
            f"{BING}/rewardsapp/reportActivity",
            params={"IG": ig, "IID": "SERP.5047", "q": word, "ajaxreq": 1},
            data={"url": url, "V": "web"},
            headers=post_headers,
            timeout=20,
        )
        return r.status_code < 400

    def do_searches(self, name: str, mobile: bool, words: list[str], delay: int) -> str:
        dash = self.userinfo()
        progress, maximum = self.counter(dash, name)
        if maximum == 0:
            return "没有这项任务（等级不够或地区不支持）"
        if progress >= maximum:
            return f"已完成 {progress}/{maximum}"

        budget = (maximum - progress) // POINTS_PER_SEARCH + 6  # 多几次，防止个别搜索不计分
        stalls = 0
        done = 0
        while done < budget and words:
            self.search_once(words.pop(), mobile)
            done += 1
            time.sleep(delay + random.uniform(0, 5))
            if done % 3 == 0:
                new_progress, maximum = self.counter(self.userinfo(), name)
                stalls = stalls + 1 if new_progress <= progress else 0
                progress = new_progress
                if progress >= maximum:
                    break
                if stalls >= 2:  # 连续 6 次搜索积分都没涨，停下
                    break
        progress, maximum = self.counter(self.userinfo(), name)
        status = "完成" if progress >= maximum else "未完成"
        return f"{status} {progress}/{maximum}（本次搜索 {done} 次）"

    # ---- 每日活动 ----
    def report_action(self) -> tuple[str, str]:
        """新版 rewards.bing.com（Next.js）用 server action 上报活动。

        action ID 每次网站发版都会变，所以每次从 /earn 页面引用的 JS 里现找。
        返回 (action ID, 部署号)，找不到时 action ID 为空。
        """
        page = self.s.get(f"{REWARDS}/earn", timeout=20).text
        page += self.s.get(f"{REWARDS}/about", timeout=20).text  # 公共 JS 也在这里，/earn 没拿到时兜底
        dpl = re.search(r"[?&](?:amp;)?dpl=([\w.-]+)", page) or re.search(r'data-dpl-id="([\w.-]+)"', page)
        chunks = list(dict.fromkeys(re.findall(r"(/_next/static/chunks/[\w\-./()]+?\.js)", page)))
        pattern = re.compile(r'createServerReference\)\("([a-f0-9]{40,64})"[^;]{0,300}?"reportActivity"\)')
        for path in chunks[:80]:
            try:
                m = pattern.search(self.s.get(f"{REWARDS}{path}", timeout=20).text)
            except Exception:  # noqa: BLE001
                continue
            if m:
                return m.group(1), dpl.group(1) if dpl else ""
        return "", dpl.group(1) if dpl else ""

    def report_once(self, action_id: str, dpl: str, promo: dict) -> bool:
        tree = ["", {"children": ["(nav)", {"children": ["earn", {"children": ["__PAGE__", {}, None, None, 0]},
                                                         None, None, 0]}, None, None, 0]}, None, None, 16]
        headers = {
            "Accept": "text/x-component",
            "Content-Type": "text/plain;charset=UTF-8",
            "Next-Action": action_id,
            "Next-Router-State-Tree": quote(json.dumps(tree, separators=(",", ":")), safe=""),
            "Origin": REWARDS,
            "Referer": f"{REWARDS}/earn",
        }
        if dpl:
            headers["X-Deployment-Id"] = dpl
        try:
            activity_type = int(promo.get("activityType") or 11)
        except (TypeError, ValueError):
            activity_type = 11
        offset = str(int(-(datetime.now().astimezone().utcoffset().total_seconds()) // 60))
        body = [
            promo["hash"],
            activity_type,
            {"offerid": promo["offerId"], "isPromotional": True if promo.get("isPromotional") else "$undefined",
             "timezoneOffset": offset},
        ]
        r = self.s.post(f"{REWARDS}/earn", data=json.dumps(body, ensure_ascii=False).encode(), headers=headers,
                        timeout=20)
        return r.status_code < 300 and bool(re.search(r"^\d+:true\s*$", r.text, re.M))

    @staticmethod
    def today_promotions(dash: dict) -> list[dict]:
        daily = dash.get("dailySetPromotions") or {}
        now = datetime.now()
        keys = {now.strftime("%m/%d/%Y"), f"{now.month}/{now.day}/{now.year}"}
        promos = [p for k, v in daily.items() if k in keys for p in (v or [])]
        if not promos and daily:  # 日期格式对不上时，取最新的一天
            promos = list(daily[sorted(daily)[-1]] or [])
        return promos + list(dash.get("morePromotions") or [])

    def do_activities(self) -> str:
        dash = self.userinfo()
        todo = [
            p
            for p in self.today_promotions(dash)
            if not p.get("complete")
            and int(p.get("priority") or 0) > -2
            and p.get("exclusiveLockedFeatureStatus") != "locked"
            and p.get("offerId")
            and p.get("hash")
        ]
        if not todo:
            return "没有待完成的活动"
        action_id, dpl = self.report_action()
        if not action_id:
            return f"待完成 {len(todo)} 个，但没在网页 JS 里找到 reportActivity（网站可能又改版了），跳过"
        print(f"reportActivity action：{action_id[:12]}…，部署号：{dpl or '未知'}")
        for p in todo:
            ok = self.report_once(action_id, dpl, p)
            print(f"上报 {p['offerId']}：{'服务器已确认' if ok else '服务器没确认'}")
            time.sleep(3)
        after = {p.get("offerId"): p for p in self.today_promotions(self.userinfo())}
        done = sum(1 for p in todo if (after.get(p["offerId"]) or {}).get("complete"))
        left = [p.get("title") or p["offerId"] for p in todo if not (after.get(p["offerId"]) or {}).get("complete")]
        text = f"完成 {done}/{len(todo)}"
        if left:
            text += f"，未完成：{'、'.join(left[:5])}（测验类第二版再做）"
        return text


# ---------------------------------------------------------------- App 端

class App:
    def __init__(self, refresh_token: str):
        self.refresh_token = refresh_token
        self.s = new_session(APP_HEADERS)

    def login(self) -> str:
        """用 refresh token 换 access token，返回微软给的新 refresh token。"""
        r = self.s.post(
            TOKEN_URL,
            data={
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "scope": SCOPE,
            },
            timeout=20,
        )
        data = safe_json(r)
        if not data.get("access_token"):
            raise ValueError(f"refresh token 已失效，请重新运行 tools/msr_token.py（{data.get('error_description') or data}）")
        self.s.headers["Authorization"] = f"Bearer {data['access_token']}"
        return data.get("refresh_token") or self.refresh_token

    def dashboard(self) -> dict:
        data = safe_json(self.s.get(f"{PLATFORM}/dapi/me", params={"channel": "SAAndroid", "options": 613}, timeout=20))
        return data.get("response") or {}

    def promotion(self, dash: dict, kind: str) -> dict | None:
        for p in dash.get("promotions") or []:
            attrs = p.get("attributes") or {}
            if attrs.get("type") == kind:
                return attrs
        return None

    def submit(self, activity_type: int, attributes: dict) -> dict:
        body = {
            "amount": 1,
            "attributes": attributes,
            "channel": "SAAndroid",
            "country": "cn",
            "id": uuid.uuid4().hex,
            "risk_context": {},
            "type": activity_type,
        }
        r = self.s.post(
            f"{PLATFORM}/dapi/me/activities",
            data=json.dumps(body),
            headers={"Content-Type": "application/json"},
            timeout=20,
        )
        return safe_json(r)

    def check_in(self) -> str:
        attrs = self.promotion(self.dashboard(), "checkin")
        if attrs is None:
            return "没有签到任务"
        if str(attrs.get("complete")).lower() == "true":
            return "今天已签到"
        before = self.dashboard().get("balance")
        self.submit(103, {})
        time.sleep(2)
        after = self.dashboard().get("balance")
        if isinstance(before, int) and isinstance(after, int) and after > before:
            return f"成功，+{after - before}"
        return f"已提交，积分没变（{before} → {after}），可能今天已签到"

    def read_to_earn(self) -> str:
        attrs = self.promotion(self.dashboard(), "msnreadearn")
        if attrs is None:
            return "没有阅读任务"
        progress = int(attrs.get("pointprogress") or attrs.get("progress") or 0)
        maximum = int(attrs.get("pointmax") or attrs.get("max") or 0)
        offer_id = attrs.get("offerid") or "ENUS_readarticle3_30points"
        if maximum and progress >= maximum:
            return f"已完成 {progress}/{maximum}"
        for _ in range(12):  # 每篇 3 分，最多 30 分；多给两次余量
            self.submit(101, {"offerid": offer_id})
            time.sleep(3)
            attrs = self.promotion(self.dashboard(), "msnreadearn") or {}
            progress = int(attrs.get("pointprogress") or attrs.get("progress") or 0)
            if maximum and progress >= maximum:
                break
        return f"{progress}/{maximum}"


# ---------------------------------------------------------------- 主流程

def handle(cookie: str, index: int) -> str:
    result: list[tuple[str, object]] = []
    web = Web(cookie)
    dash = web.userinfo()
    start = web.points(dash)
    level = ((dash.get("userStatus") or {}).get("levelInfo") or {}).get("activeLevel", "?")
    result.append(("等级", level))

    if env_bool("MSR_SEARCH", True):
        words = hot_words()
        delay = max(env_int("MSR_SEARCH_DELAY", 15), 5)
        result.append(("电脑搜索", web.do_searches("pcSearch", False, words, delay)))
        result.append(("手机搜索", web.do_searches("mobileSearch", True, words, delay)))
    result.append(("每日活动", web.do_activities()))

    tokens = get_accounts("MSR_REFRESH_TOKEN")
    if index <= len(tokens):
        old = tokens[index - 1]
        app = App(old)
        new = app.login()
        if new != old:
            saved = replace_env_value("MSR_REFRESH_TOKEN", old, new)
            if not saved:
                print(f"账号 {index} 的新 refresh token（请手动更新到 MSR_REFRESH_TOKEN）：\n{new}")
        result.append(("App 签到", app.check_in()))
        result.append(("阅读赚积分", app.read_to_earn()))
    else:
        result.append(("App 任务", "没有设置 MSR_REFRESH_TOKEN，跳过"))

    end = web.points(web.userinfo())
    result.append(("积分", f"{start} → {end}（+{end - start}）"))
    return lines(result)


if __name__ == "__main__":
    run_accounts("Microsoft Rewards", "MSR_COOKIE", handle, gap_seconds=30, accounts=get_cookie_accounts("MSR_COOKIE"))
