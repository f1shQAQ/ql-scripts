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
    MSR_SEARCH_DELAY    两次搜索之间的间隔秒数，默认 15。
    MSR_READ_DELAY      两次阅读上报之间的间隔秒数，默认 3。
    MSR_APP_PROMOTIONS  "false" 时不做其他 App 活动，默认 true。

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
from collections.abc import Callable
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
    def search_once(self, word: str, mobile: bool) -> tuple[bool, int | None]:
        """执行一次搜索上报，返回（HTTP 上报是否成功，本次确认增加的积分）。"""
        today = datetime.now().strftime("%Y-%m-%d")
        self.s.cookies.set("_Rwho", f"u={'m' if mobile else 'd'}&ts={today}", domain=".bing.com", path="/")
        headers = {"User-Agent": MOBILE_UA if mobile else PC_UA}
        cvid = uuid.uuid4().hex.upper()
        url = f"{BING}/search?q={quote(word)}&form=QBLH&cvid={cvid}"
        page = self.s.get(url, headers=headers, timeout=20)
        if page.status_code >= 400:
            return False, None
        m = re.search(r'\b["\']?IG["\']?\s*:\s*["\']([0-9A-F]{32})["\']', page.text, re.I)
        if not m:
            m = re.search(r"[?&]IG=([0-9A-F]{32})\b", page.text, re.I)
        if not m:
            return False, None
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
            params={"IG": ig, "IID": "SERP.5064", "FORM": "ANNTA1", "q": word, "cvid": cvid, "ajaxreq": 1},
            data={"url": url, "V": "web"},
            headers=post_headers,
            timeout=20,
        )
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
            time.sleep(delay + random.uniform(0, 5))
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
    def earn_page(self) -> str:
        r = self.s.get(f"{REWARDS}/earn", timeout=20)
        if "/earn" not in r.url:
            print(f"打开 /earn 被跳转到 {r.url[:80]}")
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
        page += self.s.get(f"{REWARDS}/about", timeout=20).text  # 公共 JS 也在这里，兜底
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

    def report_once(self, action_id: str, dpl: str, promo: dict) -> tuple[bool, str]:
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
        ok = r.status_code < 300 and bool(re.search(r"^\d+:true\s*$", r.text, re.M))
        detail = f"HTTP {r.status_code} {r.headers.get('content-type', '')[:30]} {r.text[:120]!r}"
        return ok, detail

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
        for i, p in enumerate(todo):
            ok, detail = self.report_once(action_id, dpl, p)
            print(f"上报 {p['offerId']}：{'服务器已确认' if ok else '服务器没确认'}")
            if not ok and i < 2:
                print(f"  响应：{detail}")
            time.sleep(3)
        after = {p.get("offerId"): p for p in self.today_promotions(self.userinfo())}
        after_web = {}
        if source == "/earn":
            after_web = {
                p["offerId"]: p
                for p in self.earn_offers(self.earn_page(), include_completed=True)
            }

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
        self.submit(103, {})
        time.sleep(2)
        after_dash = self.dashboard()
        after_attrs = self.promotion(after_dash, "checkin") or {}
        after = self.number(after_dash.get("balance"))
        gained = after - before if before is not None and after is not None else 0
        if self.complete(after_attrs) or gained > 0:
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
                time.sleep(delay)
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
                time.sleep(2)
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
            words = hot_words()
            delay = max(env_int("MSR_SEARCH_DELAY", 15), 5)
            # 两类搜索各自使用完整词表，不能让手机端继承电脑端 pop 后的剩余词。
            run_step(result, "电脑搜索", lambda: web.do_searches("pcSearch", False, words.copy(), delay))
            run_step(result, "手机搜索", lambda: web.do_searches("mobileSearch", True, words.copy(), delay))
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
    run_accounts("Microsoft Rewards", "MSR_COOKIE", handle, gap_seconds=30, accounts=get_cookie_accounts("MSR_COOKIE"))
