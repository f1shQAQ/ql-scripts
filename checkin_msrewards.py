#!/usr/bin/env python3
"""
cron: 20 9 * * *
new Env('Microsoft Rewards')

Microsoft Rewards 国区每日任务（cn.bing.com）：
    电脑端搜索、手机端搜索、每日活动（Daily Set / More Activities）、
    Bing App 每日签到、阅读赚积分、其他可上报的 App 活动。

环境变量：
    MSR_COOKIE          推荐用 Cookie-Editor 插件分别在 https://rewards.bing.com 和
                        https://login.live.com/oauth20_desktop.srf 导出 JSON，两段直接拼在一起填入。
                        有 login.live.com 的 Cookie 时，rewards 会话过期后脚本会自动续登。
                        也支持 cookies.txt 和普通 Cookie 请求头（请求头很快会过期）。
                        多个账号用 "&&" 分隔。
    MSR_REFRESH_TOKEN   可选。App 签到、阅读和其他 App 活动用的 refresh token，在本地运行 tools/msr_token.py 获取。
                        多个账号按 MSR_COOKIE 的顺序排列，用换行或 "&&" 分隔。
                        脚本会把微软返回的新 token 自动写回这个变量（需要青龙 2.18+）。
    MSR_SEARCH          "false" 时不做搜索，默认 true。
    MSR_SEARCH_DELAY    两次搜索之间的基准间隔秒数，默认 15，实际等待在对数正态上抖动。
    MSR_READ_DELAY      两次阅读上报之间的间隔秒数，默认 3。
    MSR_APP_PROMOTIONS  "false" 时不做其他 App 活动，默认 true。
    MSR_STEALTH         "false" 时退回最简请求头，只用于排查问题，默认开启。
    MSR_IMPERSONATE     "off" 时即使装了 curl_cffi 也不伪装 TLS 指纹，默认 auto。

接口参考 TheNetsky/Microsoft-Rewards-Script 和 Greasy Fork 脚本 560503。
Microsoft Rewards 条款禁止自动化，账号有被封的风险。

脚本尽量让请求看起来像正常浏览：每个账号固定一套自洽的设备档案、补全浏览器会发的
请求头和 Referer 链、节奏按对数正态抖动、被限流就立刻收手。装了 curl_cffi 时走 Chrome
的 TLS/HTTP2 指纹，没装就只能用 requests 的指纹——这些都只是减少特征，不能保证不被风控。
"""

from __future__ import annotations

import html
import json
import math
import os
import random
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote, urlparse

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

APP_HEADERS = {
    "X-Rewards-AppId": "SAAndroid/34.5.450113001",
    "X-Rewards-IsMobile": "true",
    "X-Rewards-Country": "cn",
    "X-Rewards-PartnerId": "startapp",
    "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) BingSapphire/34.5.450113001",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
POINTS_PER_SEARCH = 3
STEALTH = env_bool("MSR_STEALTH", True)

FALLBACK_WORDS = [
    "今日天气", "新闻热点", "汇率查询", "高铁时刻表", "电影推荐", "家常菜做法", "健身计划",
    "Python 教程", "旅游攻略", "手机评测", "历史上的今天", "股市行情", "英语单词", "地铁线路",
    "养花技巧", "跑步配速", "咖啡种类", "睡眠质量", "读书笔记", "相机参数", "春节习俗",
    "围棋规则", "火锅底料", "自行车保养", "宠物猫品种", "网速测试", "日历", "翻译",
    "世界地图", "天文现象", "诗词鉴赏", "数学公式", "化学元素周期表", "台风路径",
]


# ---------------------------------------------------------------- 浏览器伪装

# 一个账号在整个运行期固定一套设备档案：UA 大版本、Client Hints、平台三者必须互相
# 对得上，版本号拼错比不伪装更容易被识别。
# 版本号跟着当前稳定版走，落后一两个大版本最像一个自动更新过的真实设备；停在两年
# 前的版本本身就是特征。2026-11 稳定版是 157，过段时间记得往上调。
EDGE_MAJORS = ("155", "156", "157")
# Chromium 的 GREASE 品牌，随便挑一个真实出现过的组合即可。
GREASE_BRANDS = ('"Not_A Brand";v="8"', '"Not A(Brand";v="99"')
ANDROID_DEVICES = (("14", "Pixel 8"), ("14", "22081212C"), ("13", "SM-S9110"), ("12", "M2102K1AC"))
SEARCH_FORMS = ("QBLH", "QBRE", "QBLG")
HTML_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
    "image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
)
BLOCK_HINTS = ("captcha", "unusual traffic", "verify you are human", "too many requests")
# rewards 把未登录的请求丢给这些入口，域名和路径都换过几次，一起认。
LOGIN_HOSTS = (
    "login.live.com",
    "login.microsoftonline.com",
    "login.windows.net",
    "login.microsoft.com",
    "account.microsoft.com",
)


class Blocked(RuntimeError):
    """被限流或风控。继续重试只会更糟，直接收手。"""


@dataclass(frozen=True)
class Profile:
    ua: str
    ch_ua: str
    mobile_hint: str
    platform: str
    language: str = "zh-CN,zh;q=0.9,en;q=0.8"


def accept_encoding() -> str:
    """requests 能解哪些压缩就声明哪些，别报自己解不了的编码。"""
    try:
        from requests.utils import default_headers

        return str(default_headers().get("Accept-Encoding") or "gzip, deflate")
    except Exception:  # noqa: BLE001
        return "gzip, deflate"


ACCEPT_ENCODING = accept_encoding()


def device_set(seed_text: str = "") -> tuple[Profile, Profile]:
    """抽一套电脑 + 手机档案，共用一个 Edge 大版本，像同一个人的两台设备。

    给了 seed_text（账号的 MUID）就按「账号 + 月份」派生，同一个账号不会每天换设备；
    不给就每次随机。
    """
    rng = random.Random(f"{seed_text}:{datetime.now():%Y-%m}") if seed_text else random
    major = rng.choice(EDGE_MAJORS)
    ch_ua = f'{rng.choice(GREASE_BRANDS)}, "Chromium";v="{major}", "Microsoft Edge";v="{major}"'
    pc = Profile(
        ua=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36 Edg/{major}.0.0.0"
        ),
        ch_ua=ch_ua,
        mobile_hint="?0",
        platform="Windows",
    )
    android, model = rng.choice(ANDROID_DEVICES)
    mobile = Profile(
        ua=(
            f"Mozilla/5.0 (Linux; Android {android}; {model}) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Mobile Safari/537.36 EdgA/{major}.0.0.0"
        ),
        ch_ua=ch_ua,
        mobile_hint="?1",
        platform="Android",
    )
    return pc, mobile


def new_browser_session(profile: Profile):
    """装了 curl_cffi 就用 Chrome 的 TLS/HTTP2 指纹，没装退回 requests。"""
    mode = os.getenv("MSR_IMPERSONATE", "auto").strip().lower()
    if STEALTH and mode != "off":
        try:
            from curl_cffi import requests as curl_requests
        except ImportError:
            curl_requests = None
        if curl_requests is not None:
            try:
                session = curl_requests.Session(impersonate="chrome")
                session.headers.update({"User-Agent": profile.ua, "Accept-Language": profile.language})
                print("传输层：curl_cffi（Chrome 指纹）")
                return session
            except Exception as e:  # noqa: BLE001
                print(f"curl_cffi 初始化失败，退回 requests：{type(e).__name__}: {e}")
    if STEALTH:
        print("传输层：requests（TLS 指纹伪装不了）")
    return new_session({"User-Agent": profile.ua, "Accept-Language": profile.language})


def set_cookie(session, name: str, value: str, domain: str) -> None:
    session.cookies.set(name, value, domain=domain, path="/")


def _base_headers(profile: Profile) -> dict[str, str]:
    if not STEALTH:
        return {"User-Agent": profile.ua}
    return {
        "User-Agent": profile.ua,
        "Accept-Language": profile.language,
        "Accept-Encoding": ACCEPT_ENCODING,
        "sec-ch-ua": profile.ch_ua,
        "sec-ch-ua-mobile": profile.mobile_hint,
        "sec-ch-ua-platform": f'"{profile.platform}"',
        "DNT": "1",
    }


def nav_headers(profile: Profile, referer: str | None = None, site: str = "same-origin") -> dict[str, str]:
    """地址栏跳转（GET 页面）时浏览器会发的头。"""
    headers = _base_headers(profile)
    if STEALTH:
        headers.update(
            {
                "Accept": HTML_ACCEPT,
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": site,
                "Sec-Fetch-User": "?1",
            }
        )
    if referer:
        headers["Referer"] = referer
    return headers


def xhr_headers(
    profile: Profile,
    referer: str,
    origin: str | None = None,
    requested_with: bool = True,
    dest: str = "empty",
) -> dict[str, str]:
    """页面脚本发起 fetch / XHR 时的头。

    Origin 只在非 GET 请求里带（Fetch 规范如此），GET 带上反而露馅。
    """
    headers = _base_headers(profile)
    if STEALTH:
        headers.update(
            {
                "Accept": "*/*",
                "Sec-Fetch-Dest": dest,
                "Sec-Fetch-Mode": "cors" if dest == "empty" else "no-cors",
                "Sec-Fetch-Site": "same-origin",
            }
        )
    if origin:
        headers["Origin"] = origin
    headers["Referer"] = referer
    if requested_with:
        headers["X-Requested-With"] = "XMLHttpRequest"
    return headers


def human_sleep(seconds: float, spread: float = 0.35) -> None:
    """对数正态抖动。固定间隔、均匀分布本身就是特征。"""
    base = max(float(seconds), 1.0)
    try:
        value = random.lognormvariate(math.log(base), spread)
    except (ValueError, OverflowError):
        value = base
    time.sleep(min(max(value, base * 0.5), base * 1.8))


def idle_pause() -> None:
    """小概率走开一会儿，让整串请求里出现真人会有的空档。"""
    if STEALTH and random.random() < 0.12:
        time.sleep(random.uniform(25, 80))


def login_redirect(url: str) -> bool:
    """这个地址是不是被踢回了登录页。"""
    low = url.lower()
    if any(h in low for h in LOGIN_HOSTS):
        return True
    return "/oauth20" in low or "oauth2/v2.0/authorize" in low


def block_reason(status: int, text: str) -> str:
    """命中限流或验证码时返回一句话说明，没命中返回空串。"""
    if status in (403, 418, 429):
        return f"HTTP {status}"
    low = text[:6000].lower()
    for hint in BLOCK_HINTS:
        if hint in low:
            return hint
    return ""


# ---------------------------------------------------------------- 搜索词

def hot_words(ua: str = "") -> list[str]:
    """取百度、微博热搜做搜索词，都失败时用内置词表。"""
    words: list[str] = []
    s = new_session({"User-Agent": ua} if ua else None)
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
        self.cookies = parse_cookie_export(cookie_text, ".bing.com")
        # 用 MUID 当种子，同一个账号固定一套设备档案，不会每次运行都换一台「新电脑」。
        seed = next((c["value"] for c in self.cookies if c["name"] == "MUID"), "")
        self.pc, self.mobile = device_set(seed)
        self.s = new_browser_session(self.pc)
        self.last_url = ""
        self.sso_tried = False
        for c in self.cookies:
            self.s.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"], secure=c["secure"])
        self.has_live = any("live.com" in c["domain"] for c in self.cookies)
        domains: dict[str, int] = {}
        for c in self.cookies:
            domains[c["domain"]] = domains.get(c["domain"], 0) + 1
        print("读到的 Cookie：" + "，".join(f"{d} {n} 个" for d, n in domains.items()))
        print(f"浏览器伪装：{'关闭' if not STEALTH else '开启'}，设备 {self.pc.platform} / {self.mobile.platform}")
        if not any(c["name"] == "_U" for c in self.cookies):
            print("提示：没有 _U，Bing 搜索可能不计分")
        missing = self.missing_cookies()
        if missing:
            print("提示：缺少登录 Cookie " + "、".join(missing) + "，网页端大概率会报登录失效")
        dead = self.placeholder_cookies()
        if dead:
            print(
                f"提示：{'/'.join(dead)} 是 Disabled 占位值 —— 这次登录没有建立持久登录。"
                "常见原因：无痕窗口、没勾「保持登录」、浏览器关闭时清除 Cookie、或拦了第三方 Cookie"
            )

    def missing_cookies(self) -> list[str]:
        """导出里应该有的登录态 Cookie，缺了就说明这份导出不全。"""
        names = {c["name"] for c in self.cookies}
        return [n for n in ("_U", "RPSSecAuth", "MSPAuth", "_C_Auth") if n not in names]

    def placeholder_cookies(self) -> list[str]:
        """微软在未登录时会把 MSPAuth / MSPProf 写成 Disabled，这种值不可能是有效票据。"""
        return [
            c["name"]
            for c in self.cookies
            if c["name"] in ("MSPAuth", "MSPProf") and c["value"].strip().lower() == "disabled"
        ]

    def warm_up(self) -> None:
        """先开一次首页，让 Bing 自己下发 MUID、SRCHHPGUSR 等 Cookie，再开始搜索。"""
        try:
            self.s.get(f"{BING}/", headers=nav_headers(self.pc, site="none"), timeout=20)
        except Exception as e:  # noqa: BLE001
            print(f"预热首页失败（不影响后续）：{type(e).__name__}: {e}")
        self.last_url = f"{BING}/"
        human_sleep(3)

    def _get_userinfo(self):
        return self.s.get(
            f"{REWARDS}/api/getuserinfo",
            params={"type": 1, "X-Requested-With": "XMLHttpRequest", "_": int(time.time() * 1000)},
            headers=xhr_headers(self.pc, f"{REWARDS}/"),
            timeout=20,
        )

    def _login_expired(self, detail: str) -> ValueError:
        miss = self.missing_cookies()
        dead = self.placeholder_cookies()
        hint = f"；这份导出还缺 {'、'.join(miss)}" if miss else ""
        if dead:
            hint += f"；{'/'.join(dead)} 是 Disabled，说明这次登录没有持久票据"
        return ValueError(
            f"{detail}{hint}。这组 Cookie 已经不能换到登录态了，"
            "通常是对应的登录态已过期、账号在别处被登出，或微软要求重新验证。"
            "请在浏览器（建议无痕窗口）重新登录 rewards.bing.com，按 README 重新导出 Cookie 再跑；"
            "连续失败时先别再跑，反复尝试只会让风控更紧"
        )

    def sso(self) -> None:
        """rewards.bing.com 的会话过期后，用 login.live.com 的登录态静默续登，和浏览器自动跳转的过程一样。"""
        if self.sso_tried:  # 一次运行只试一次，别把 login.live.com 当重试通道
            raise self._login_expired("静默续登已经失败过")
        self.sso_tried = True
        r = self.s.get(
            f"{REWARDS}/Signin",
            params={"idru": "/", "silent": "True"},
            headers=nav_headers(self.pc, f"{REWARDS}/", "same-site"),
            timeout=20,
        )
        for _ in range(4):
            form = re.search(r"<form[^>]*\baction=\"([^\"]+)\"[^>]*>(.*?)</form>", r.text, re.S | re.I)
            if not form:
                break
            fields: dict[str, str] = {}
            for tag in re.findall(r"<input[^>]*>", form.group(2), re.I):
                name = re.search(r'\bname="([^"]*)"', tag)
                value = re.search(r'\bvalue="([^"]*)"', tag)
                if name:
                    fields[name.group(1)] = html.unescape(value.group(1)) if value else ""
            if fields.get("error"):
                raise self._login_expired(f"login.live.com 拒绝静默登录（{fields['error']}）")
            r = self.s.post(html.unescape(form.group(1)), data=fields, timeout=20)
        if "rewards.bing.com" not in str(r.url):
            raise self._login_expired(f"静默登录后停在 {urlparse(str(r.url)).netloc or '未知页面'}，没有回到 rewards")

    def userinfo(self) -> dict:
        resp = self._get_userinfo()
        if resp.status_code == 401:
            if not self.has_live:
                raise self._login_expired(
                    "rewards.bing.com 返回 HTTP 401，且 Cookie 里没有 login.live.com 的部分（无法续登）"
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
    def search_once(self, word: str, mobile: bool) -> tuple[bool, int | None]:
        """执行一次搜索上报，返回（HTTP 上报是否成功，本次确认增加的积分）。"""
        profile = self.mobile if mobile else self.pc
        cvid = uuid.uuid4().hex.upper()
        # form 每次随机，真实的 Bing 入口不止一种；URL 结构保持和地址栏一致。
        url = f"{BING}/search?q={quote(word)}&form={random.choice(SEARCH_FORMS)}&cvid={cvid}"
        # 多数时候从上一页点过来，偶尔当作新开标签页，Referer 链不该永远是空的。
        referer = self.last_url if self.last_url and random.random() > 0.2 else None
        page = self.s.get(
            url,
            headers=nav_headers(profile, referer, "same-origin" if referer else "none"),
            timeout=20,
        )
        hit = block_reason(page.status_code, page.text)
        if hit:
            raise Blocked(f"搜索页被拦截（{hit}）")
        if page.status_code >= 400:
            return False, None
        m = re.search(r'\b["\']?IG["\']?\s*:\s*["\']([0-9A-F]{32})["\']', page.text, re.I)
        if not m:
            m = re.search(r"[?&]IG=([0-9A-F]{32})\b", page.text, re.I)
        if not m:
            return False, None
        ig = m.group(1)
        self.last_url = url
        post_headers = xhr_headers(profile, url, BING)
        post_headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        self.s.post(
            f"{BING}/rewardsapp/ncheader",
            params={"ver": "88888888", "IID": "SERP.5047", "IG": ig, "ajaxreq": 1},
            data="wb=1%3bi%3d1%3bv%3d1",
            headers=post_headers,
            timeout=20,
        )
        r = self.s.post(
            f"{BING}/rewardsapp/reportActivity",
            params={"IG": ig, "IID": "SERP.5064", "FORM": "ANNTA1", "q": word, "cvid": cvid, "ajaxreq": 1},
            data={"url": url, "V": "web"},
            headers=post_headers,
            timeout=20,
        )
        hit = block_reason(r.status_code, r.text)
        if hit:
            raise Blocked(f"上报接口被拦截（{hit}）")
        gained = None
        match = re.search(r"ModernRewards\.ReportActivity\((\{[\s\S]*?\})\)\s*;", r.text)
        if match:
            try:
                session = (json.loads(match.group(1)).get("RewardsSessionData") or {})
                balance, previous = session.get("Balance"), session.get("PreviousBalance")
                if isinstance(balance, (int, float)) and isinstance(previous, (int, float)):
                    gained = int(balance - previous)
            except (ValueError, TypeError, AttributeError):
                pass
        return r.status_code < 400, gained

    def do_searches(self, name: str, mobile: bool, words: list[str], delay: int) -> str:
        dash = self.userinfo()
        progress, maximum = self.counter(dash, name)
        if maximum == 0:
            return "没有这项任务（等级不够或地区不支持）"
        if progress >= maximum:
            return f"已完成 {progress}/{maximum}"

        # 电脑端和手机端各自再打乱一次，否则两端会用完全相同的顺序搜同一批词。
        random.shuffle(words)
        # 浏览器在当天首次搜索时写一次 _Rwho；两端各写各的值，不该每次请求都改。
        today = datetime.now().strftime("%Y-%m-%d")
        set_cookie(self.s, "_Rwho", f"u={'m' if mobile else 'd'}&ts={today}", ".bing.com")

        # 向上取整，避免剩余积分不是 3 的倍数时少算一次；额外余量用于不计分的搜索。
        budget = (maximum - progress + POINTS_PER_SEARCH - 1) // POINTS_PER_SEARCH + 6
        stalls = 0
        attempts = 0
        accepted = 0
        checked_accepted = 0
        credited = 0
        consecutive_failures = 0
        stop_reason = ""
        while attempts < budget and words:
            word = words.pop()
            attempts += 1
            try:
                ok, gained = self.search_once(word, mobile)
            except Blocked as e:
                # 已经被盯上了就别再补请求，停下比刷满更划算。
                stop_reason = f"触发风控（{e}），停止后续搜索"
                print(stop_reason)
                break
            except Exception as e:  # 单次网络错误不应终止其他搜索和后续任务
                ok, gained = False, None
                print(f"搜索请求异常（{word}）：{type(e).__name__}: {e}")
            if ok:
                accepted += 1
                consecutive_failures = 0
                credited += gained if gained and gained > 0 else 0
            else:
                consecutive_failures += 1
                print(f"搜索未成功上报：{word}")
                if consecutive_failures >= 3:
                    stop_reason = "连续 3 次上报失败"
                    break
            human_sleep(delay)
            idle_pause()
            if accepted > checked_accepted and (accepted % 3 == 0 or bool(gained and gained > 0)):
                checked_accepted = accepted
                new_progress, maximum = self.counter(self.userinfo(), name)
                stalls = stalls + 1 if new_progress <= progress else 0
                progress = new_progress
                if progress >= maximum:
                    break
                if stalls >= 2:  # 连续 6 次已上报搜索积分都没涨，停下
                    stop_reason = "连续 6 次搜索后计数未增长"
                    break
        else:
            if not words and attempts < budget:
                stop_reason = "搜索词已用完"

        progress, maximum = self.counter(self.userinfo(), name)
        status = "完成" if progress >= maximum else "未完成"
        detail = f"本次尝试 {attempts} 次，成功上报 {accepted} 次"
        if credited:
            detail += f"，上报确认 +{credited} 分"
        if stop_reason and progress < maximum:
            detail += f"，{stop_reason}"
        return f"{status} {progress}/{maximum}（{detail}）"

    # ---- 每日活动 ----
    def earn_page(self, retry: bool = True) -> str:
        """取 /earn 页面。会话过期时会先试着静默续登一次，续不上就报错，别拿失效会话硬发上报。"""
        r = self.s.get(
            f"{REWARDS}/earn",
            headers=nav_headers(self.pc, self.last_url or f"{BING}/", "same-site"),
            timeout=20,
        )
        url = str(r.url)
        if login_redirect(url) or "rewards.bing.com" not in url:
            if retry and self.has_live and not self.sso_tried:
                print("rewards 网页会话过期，试一次静默续登")
                self.sso()
                return self.earn_page(retry=False)
            where = urlparse(url).netloc or url[:50]
            raise self._login_expired(
                f"rewards.bing.com 网页会话已失效（/earn 被跳到 {where}）。"
                "搜索走的是接口还能用，但每日活动必须在登录状态下上报"
            )
        if "/earn" not in url:
            print(f"打开 /earn 被跳转到 {url[:80]}")
        self.last_url = url
        return r.text

    @staticmethod
    def flight_text(page: str) -> str:
        """把页面里 self.__next_f.push([1,"..."]) 的 RSC 数据解码并拼起来。"""
        parts = []
        for m in re.finditer(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', page):
            try:
                parts.append(json.loads(m.group(1)))
            except ValueError:
                continue
        return "".join(parts)

    @staticmethod
    def _enclosing_object(text: str, pos: int) -> dict | None:
        """找出包住 pos 的最小 JSON 对象。"""
        depth = 0
        for start in range(pos, max(pos - 20000, -1), -1):
            ch = text[start]
            if ch == "}":
                depth += 1
            elif ch == "{":
                if depth == 0:
                    try:
                        obj, _ = json.JSONDecoder().raw_decode(text, start)
                        return obj if isinstance(obj, dict) else None
                    except ValueError:
                        return None
                depth -= 1
        return None

    def earn_offers(self, page: str, include_completed: bool = False) -> list[dict]:
        """从 /earn 页面的 RSC 数据里取任务（含新版 hash），取不到时返回空列表。"""
        text = self.flight_text(page)
        today = datetime.now().strftime("%Y-%m-%d")
        offers: dict[str, dict] = {}
        for m in re.finditer(r'"offerId":"', text):
            obj = self._enclosing_object(text, m.start())
            if not obj or not obj.get("offerId"):
                continue
            cur = offers.setdefault(obj["offerId"], {})
            for k, v in obj.items():
                if v not in (None, "") and k not in cur:
                    cur[k] = v
        result = []
        for o in offers.values():
            done = o.get("isCompleted") is True or o.get("complete") is True
            date = str(o.get("date") or "")[:10]
            if (done and not include_completed) or o.get("isLocked") is True or not o.get("hash"):
                continue
            if date and re.match(r"\d{4}-\d{2}-\d{2}$", date) and date > today:
                continue
            result.append(o)
        return result

    def report_action(self, page: str) -> tuple[str, str]:
        """新版 rewards.bing.com（Next.js）用 server action 上报活动。

        action ID 每次网站发版都会变，所以每次从页面引用的 JS 里现找。
        返回 (action ID, 部署号)，找不到时 action ID 为空。
        """
        about = self.s.get(
            f"{REWARDS}/about",
            headers=nav_headers(self.pc, f"{REWARDS}/", "same-origin"),
            timeout=20,
        )
        page += about.text  # 公共 JS 也在这里，兜底
        dpl = re.search(r"[?&](?:amp;)?dpl=([\w.-]+)", page) or re.search(r'data-dpl-id="([\w.-]+)"', page)
        chunks = list(dict.fromkeys(re.findall(r"(/_next/static/chunks/[\w\-./()]+?\.js)", page)))
        pattern = re.compile(r'createServerReference\)\("([a-f0-9]{40,64})"[^;]{0,300}?"reportActivity"\)')
        for path in chunks[:80]:
            try:
                r = self.s.get(
                    f"{REWARDS}{path}",
                    headers=xhr_headers(self.pc, f"{REWARDS}/earn", requested_with=False, dest="script"),
                    timeout=20,
                )
            except Exception:  # noqa: BLE001
                continue
            m = pattern.search(r.text)
            if m:
                return m.group(1), dpl.group(1) if dpl else ""
        return "", dpl.group(1) if dpl else ""

    def report_once(self, action_id: str, dpl: str, promo: dict) -> tuple[bool, str]:
        tree = ["", {"children": ["(nav)", {"children": ["earn", {"children": ["__PAGE__", {}, None, None, 0]},
                                                         None, None, 0]}, None, None, 0]}, None, None, 16]
        # server action 走 fetch，不带 X-Requested-With，Accept 是 RSC 的 text/x-component。
        headers = xhr_headers(self.pc, f"{REWARDS}/earn", REWARDS, requested_with=False)
        headers.update(
            {
                "Accept": "text/x-component",
                "Content-Type": "text/plain;charset=UTF-8",
                "Next-Action": action_id,
                "Next-Router-State-Tree": quote(json.dumps(tree, separators=(",", ":")), safe=""),
            }
        )
        if dpl:
            headers["X-Deployment-Id"] = dpl
        attrs = promo.get("attributes") if isinstance(promo.get("attributes"), dict) else {}
        try:
            activity_type = int(promo.get("activityType") or attrs.get("activityType") or 11)
        except (TypeError, ValueError):
            activity_type = 11
        promotional = promo.get("isPromotional")
        if promotional is None:
            promotional = attrs.get("promotional")
        promotional = promotional is True or str(promotional).lower() == "true"
        offset = str(int(-(datetime.now().astimezone().utcoffset().total_seconds()) // 60))
        body = [
            promo["hash"],
            activity_type,
            {"offerid": promo["offerId"], "isPromotional": True if promotional else "$undefined",
             "timezoneOffset": offset},
        ]
        r = self.s.post(f"{REWARDS}/earn", data=json.dumps(body, ensure_ascii=False).encode(), headers=headers,
                        timeout=20, allow_redirects=False)
        ok, reason = self.action_result(r.status_code, r.text)
        detail = f"{reason}（HTTP {r.status_code} {r.text[:120]!r}）"
        return ok, detail

    @staticmethod
    def action_result(status: int, text: str) -> tuple[bool, str]:
        """server action 的响应是 RSC，只有 `1:true` 才算服务器真的认了。

        会话失效时服务端不会报错，而是返回重定向（`$@` 引用）或 `1:false`，
        这两种要区分开：前者别再往下试，后者说明活动本身没通过。
        """
        if status >= 400:
            return False, f"HTTP {status}"
        if re.search(r"^\d+:true\s*$", text, re.M):
            return True, "服务器已确认"
        if (
            "login.live.com" in text
            or "NEXT_REDIRECT" in text
            or re.search(r'"a"\s*:\s*"\$@', text)  # RSC 的重定向记录：{"a":"$@...","i":false}
        ):
            return False, "网页会话已失效，服务端把上报重定向到了登录页"
        if re.search(r"^\d+:false\s*$", text, re.M):
            return False, "服务端返回 false（活动无效或已领过）"
        return False, "响应里没有确认标记"

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
        page = self.earn_page()
        todo = self.earn_offers(page)
        source = "/earn"
        if not todo:  # 新版页面没解析出来时，用旧接口的数据
            source = "getuserinfo"
            todo = [
                p
                for p in self.today_promotions(self.userinfo())
                if not p.get("complete")
                and int(p.get("priority") or 0) > -2
                and p.get("exclusiveLockedFeatureStatus") != "locked"
                and p.get("offerId")
                and p.get("hash")
            ]
        if not todo:
            return "没有待完成的活动"
        print(f"待完成活动 {len(todo)} 个（来源：{source}）")
        action_id, dpl = self.report_action(page)
        if not action_id:
            return f"待完成 {len(todo)} 个，但没在网页 JS 里找到 reportActivity（网站可能又改版了），跳过"
        print(f"reportActivity action：{action_id[:12]}…，部署号：{dpl or '未知'}")
        blocked_at = ""
        for i, p in enumerate(todo):
            ok, detail = self.report_once(action_id, dpl, p)
            print(f"上报 {p['offerId']}：{'服务器已确认' if ok else detail.split('（')[0]}")
            if not ok and i < 2:
                print(f"  响应：{detail}")
            if not ok and "会话已失效" in detail:
                # 会话都掉了，剩下的活动再发也是白打，直接收手并说明原因。
                blocked_at = "网页会话已失效，停止剩余活动，请重新导出 Cookie"
                print(blocked_at)
                break
            if not ok and ("HTTP 403" in detail or "HTTP 429" in detail):
                blocked_at = f"第 {i + 1} 个活动被限流，停止剩余活动"
                print(blocked_at)
                break
            human_sleep(3)
            idle_pause()
        after = {p.get("offerId"): p for p in self.today_promotions(self.userinfo())}
        after_web = {}
        if source == "/earn":
            try:
                after_web = {
                    p["offerId"]: p
                    for p in self.earn_offers(self.earn_page(), include_completed=True)
                }
            except Exception as e:  # 复读失败不该把已经做过的活动一起算丢
                print(f"复读 /earn 失败，只用接口状态判断：{type(e).__name__}: {e}")

        def finished(p: dict) -> bool:
            current = after.get(p["offerId"]) or {}
            current_web = after_web.get(p["offerId"]) or {}
            # 解析不到后置状态时必须保持“未确认”，不能把空列表当成全部完成。
            return bool(
                current.get("complete") is True
                or current.get("isCompleted") is True
                or current_web.get("complete") is True
                or current_web.get("isCompleted") is True
            )

        done = sum(1 for p in todo if finished(p))
        left = [p.get("title") or p["offerId"] for p in todo if not finished(p)]
        text = f"确认完成 {done}/{len(todo)}"
        if left:
            text += f"，未确认：{'、'.join(left[:5])}"
        if blocked_at:
            text += f"，{blocked_at}"
        return text


# ---------------------------------------------------------------- App 端

class App:
    def __init__(self, refresh_token: str):
        self.refresh_token = refresh_token
        self.s = new_session(APP_HEADERS)

    @staticmethod
    def number(value: object) -> int | None:
        """接口的数字字段有时是字符串；无效值不能悄悄当成 0。"""
        if isinstance(value, bool) or value in (None, ""):
            return None
        try:
            return int(float(str(value)))
        except (TypeError, ValueError):
            return None

    @classmethod
    def complete(cls, attrs: dict) -> bool:
        progress = cls.number(attrs.get("pointprogress") or attrs.get("progress"))
        maximum = cls.number(attrs.get("pointmax") or attrs.get("max"))
        return str(attrs.get("complete")).strip().lower() == "true" or bool(
            maximum and maximum > 0 and progress is not None and progress >= maximum
        )

    @staticmethod
    def offer_id(promotion: dict) -> str:
        attrs = promotion.get("attributes") or {}
        return str(attrs.get("offerid") or promotion.get("name") or "").strip()

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
        r = self.s.get(f"{PLATFORM}/dapi/me", params={"channel": "SAAndroid", "options": 613}, timeout=20)
        data = safe_json(r)
        response = data.get("response")
        if r.status_code >= 400 or data.get("code") != 0 or not isinstance(response, dict):
            detail = data.get("message") or data.get("error") or data.get("_raw") or str(data)[:150]
            raise ValueError(f"App 面板读取失败（HTTP {r.status_code}，code={data.get('code')}）：{detail}")
        return response

    def promotion(self, dash: dict, kind: str) -> dict | None:
        for p in dash.get("promotions") or []:
            attrs = p.get("attributes") or {}
            if str(attrs.get("type") or "").lower() == kind:
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
        data = safe_json(r)
        if r.status_code >= 400 or data.get("code") != 0:
            detail = data.get("message") or data.get("error") or data.get("_raw") or str(data)[:150]
            raise ValueError(f"活动上报失败（HTTP {r.status_code}，code={data.get('code')}）：{detail}")
        response = data.get("response")
        return response if isinstance(response, dict) else {}

    def check_in(self) -> str:
        dash = self.dashboard()
        attrs = self.promotion(dash, "checkin")
        if attrs is None:
            return "没有签到任务"
        if self.complete(attrs):
            return "今天已签到"
        before = self.number(dash.get("balance"))
        # 其他 App 活动上报都带 offerid，签到也带上；接口没给就退回原来的空属性。
        offer_id = str(attrs.get("offerid") or "").strip()
        self.submit(103, {"offerid": offer_id} if offer_id else {})
        after = before
        confirmed = False
        for wait in (3, 8):  # 面板有缓存，第一次没确认时隔几秒再看一次
            human_sleep(wait, 0.4)
            after_dash = self.dashboard()
            after_attrs = self.promotion(after_dash, "checkin") or {}
            after = self.number(after_dash.get("balance"))
            if self.complete(after_attrs) or (
                before is not None and after is not None and after > before
            ):
                confirmed = True
                break
        gained = after - before if before is not None and after is not None else 0
        if confirmed:
            return f"成功，+{gained}" if gained > 0 else "成功，任务状态已确认"
        return f"已提交但未确认计分（{before} → {after}），请稍后核对面板"

    def read_to_earn(self) -> str:
        dash = self.dashboard()
        attrs = self.promotion(dash, "msnreadearn")
        if attrs is None:
            return "没有阅读任务"
        progress = self.number(attrs.get("pointprogress") or attrs.get("progress")) or 0
        maximum = self.number(attrs.get("pointmax") or attrs.get("max")) or 0
        offer_id = str(attrs.get("offerid") or "ENUS_readarticle3_30points")
        if self.complete(attrs):
            return f"已完成 {progress}/{maximum or '?'}"
        balance = self.number(dash.get("balance"))
        delay = max(env_int("MSR_READ_DELAY", 3), 1)
        attempts = 0
        accepted = 0
        earned = 0
        stop_reason = ""
        finished = False
        # 每次都用进度或余额验证；无增长立即停止，不再机械地提交满 12 次。
        for _ in range(20):
            attempts += 1
            try:
                response = self.submit(101, {"offerid": offer_id})
                human_sleep(delay, 0.5)
                after_dash = self.dashboard()
            except Exception as e:  # 保留已获得进度，并把失败原因带回通知
                stop_reason = f"第 {attempts} 次上报失败：{type(e).__name__}: {e}"
                break
            after_attrs = self.promotion(after_dash, "msnreadearn")
            if after_attrs is None:
                stop_reason = "上报后任务从面板消失，无法确认"
                break
            after_balance = self.number(response.get("balance"))
            if after_balance is None:
                after_balance = self.number(after_dash.get("balance"))
            gained = after_balance - balance if balance is not None and after_balance is not None else 0
            new_progress = self.number(after_attrs.get("pointprogress") or after_attrs.get("progress"))
            if new_progress is None:
                new_progress = progress
            progressed = new_progress > progress or gained > 0
            if progressed:
                accepted += 1
                earned += max(gained, 0)
            progress = new_progress
            maximum = self.number(after_attrs.get("pointmax") or after_attrs.get("max")) or maximum
            if after_balance is not None:
                balance = after_balance
            if self.complete(after_attrs):
                finished = True
                break
            if not progressed:
                stop_reason = "积分和进度均未增长，已停止无效上报"
                break

        status = "完成" if finished else "未完成"
        detail = f"本次有效 {accepted}/{attempts} 篇，确认 +{earned} 分"
        if stop_reason:
            detail += f"，{stop_reason}"
        return f"{status} {progress}/{maximum or '?'}（{detail}）"

    def app_promotions(self) -> str:
        """完成面板标记为 sapphire 的其他 App 点击活动；签到和阅读由专门流程处理。"""
        dash = self.dashboard()
        pending = []
        for promotion in dash.get("promotions") or []:
            attrs = promotion.get("attributes") or {}
            if (
                str(attrs.get("type") or "").strip().lower() == "sapphire"
                and self.offer_id(promotion)
                and not self.complete(attrs)
            ):
                pending.append(promotion)
        if not pending:
            return "没有待完成的 App 活动"

        done = 0
        earned = 0
        left = []
        for promotion in pending:
            offer_id = self.offer_id(promotion)
            before_attrs = promotion.get("attributes") or {}
            before_progress = self.number(before_attrs.get("pointprogress") or before_attrs.get("progress")) or 0
            before_balance = self.number(dash.get("balance"))
            try:
                self.submit(101, {"offerid": offer_id})
                human_sleep(2, 0.5)
                dash = self.dashboard()
            except Exception as e:
                print(f"App 活动 {offer_id} 失败：{type(e).__name__}: {e}")
                left.append(offer_id)
                continue
            current = next(
                (p for p in dash.get("promotions") or [] if self.offer_id(p).lower() == offer_id.lower()),
                None,
            )
            after_attrs = (current or {}).get("attributes") or {}
            after_progress = self.number(after_attrs.get("pointprogress") or after_attrs.get("progress")) or 0
            after_balance = self.number(dash.get("balance"))
            gained = after_balance - before_balance if before_balance is not None and after_balance is not None else 0
            if self.complete(after_attrs) or after_progress > before_progress or gained > 0:
                done += 1
                earned += max(gained, 0)
            else:
                left.append(offer_id)
        text = f"确认完成 {done}/{len(pending)}"
        if earned:
            text += f"，+{earned} 分"
        if left:
            text += f"，未确认：{'、'.join(left[:5])}"
        return text


# ---------------------------------------------------------------- 主流程

def run_step(result: list[tuple[str, object]], name: str, func: Callable[[], object]) -> None:
    """单项失败只影响该项，避免后面的任务被一起跳过。"""
    try:
        result.append((name, func()))
    except Exception as e:  # noqa: BLE001
        print(f"{name}失败：{type(e).__name__}: {e}")
        result.append((name, f"失败：{type(e).__name__}: {e}"))


def handle(cookie: str, index: int) -> str:
    result: list[tuple[str, object]] = []
    web = None
    start = None
    if STEALTH:
        # 多账号别在同一秒整整齐齐开始，每个账号有自己的起点。
        time.sleep(random.uniform(2, 25))
    try:
        web = Web(cookie)
        dash = web.userinfo()
        start = web.points(dash)
        level = ((dash.get("userStatus") or {}).get("levelInfo") or {}).get("activeLevel", "?")
        result.append(("等级", level))
    except Exception as e:  # 网页登录态失败时仍尝试独立的 App 任务
        print(f"网页端初始化失败：{type(e).__name__}: {e}")
        result.append(("网页端", f"不可用：{type(e).__name__}: {e}"))

    if web is not None and start is not None:
        if env_bool("MSR_SEARCH", True):
            web.warm_up()  # 登录态确认可用之后再预热首页，别拿已经失效的会话去开页面
            words = hot_words(web.pc.ua)
            delay = max(env_int("MSR_SEARCH_DELAY", 15), 5)
            # 真人不一定先电脑后手机，每次随机；两类搜索各用完整词表，不互相继承 pop 后的剩余。
            groups = [("电脑搜索", "pcSearch", False), ("手机搜索", "mobileSearch", True)]
            if random.random() < 0.5:
                groups.reverse()
            for label, counter_name, mobile in groups:
                run_step(
                    result,
                    label,
                    lambda c=counter_name, m=mobile: web.do_searches(c, m, words.copy(), delay),
                )
        run_step(result, "每日活动", web.do_activities)

    tokens = get_accounts("MSR_REFRESH_TOKEN")
    if index <= len(tokens):
        old = tokens[index - 1]
        app = App(old)
        try:
            new = app.login()
            if new != old:
                saved = replace_env_value("MSR_REFRESH_TOKEN", old, new)
                if not saved:
                    print(f"账号 {index} 的新 refresh token（请手动更新到 MSR_REFRESH_TOKEN）：\n{new}")
            run_step(result, "App 签到", app.check_in)
            run_step(result, "阅读赚积分", app.read_to_earn)
            if env_bool("MSR_APP_PROMOTIONS", True):
                run_step(result, "App 活动", app.app_promotions)
        except Exception as e:  # token 失效只跳过 App，不影响已经完成的网页任务
            print(f"App 登录失败：{type(e).__name__}: {e}")
            result.append(("App 任务", f"登录失败：{type(e).__name__}: {e}"))
    else:
        result.append(("App 任务", "没有设置 MSR_REFRESH_TOKEN，跳过"))

    if web is not None and start is not None:
        try:
            end = web.points(web.userinfo())
            result.append(("积分", f"{start} → {end}（+{end - start}）"))
        except Exception as e:
            result.append(("积分", f"起始 {start}，结束余额读取失败：{type(e).__name__}: {e}"))
    return lines(result)


if __name__ == "__main__":
    # 账号之间至少隔一分钟，别让同一出口 IP 上几个账号首尾相接。
    run_accounts(
        "Microsoft Rewards",
        "MSR_COOKIE",
        handle,
        gap_seconds=random.randint(60, 180) if STEALTH else 30,
        accounts=get_cookie_accounts("MSR_COOKIE"),
    )
