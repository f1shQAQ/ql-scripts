# ql-scripts

自用的青龙面板签到脚本。

| 脚本 | 任务 | 默认定时 | 环境变量 |
|---|---|---|---|
| `checkin_baiduwp.py` | 百度网盘会员签到 + 每日答题 | 每天 08:15 | `BAIDUWP_COOKIE` |
| `checkin_workbuddy.py` | WorkBuddy「Buddy 加油站」每日签到领积分 | 每天 09:10 | `WB_TOKEN` |
| `checkin_bilibili.py` | B站漫画签到、直播签到、观看、分享，可选投币 | 每天 08:30 | `BILI_COOKIE` 等 |
| `checkin_xiaoheihe.py` | 小黑盒签到 + 浏览/分享任务，汇报任务进度和 H 币 | 每天 08:45 | `XHH_COOKIE` |
| `checkin_msrewards.py` | Microsoft Rewards 国区：搜索、每日活动、App 签到、阅读及其他 App 活动 | 每天 09:20 | `MSR_COOKIE` 等 |
| `checkin_nodeseek.py` | NodeSeek / DeepFlood 每日签到领鸡腿 | 每天 00:07 | `NS_COOKIE` 等 |

`qlutils.py` 是公共模块，不是任务。

## 拉取到青龙

在青龙「订阅管理」里新建订阅，或者在任务里执行：

```bash
ql repo https://github.com/f1shQAQ/ql-scripts.git "checkin_" "" "qlutils" "main" "py"
```

参数依次是：仓库地址、白名单（只把 `checkin_` 开头的文件建成任务）、黑名单、依赖文件、分支、文件后缀。

依赖：在「依赖管理 → Python3」里添加 `requests`（青龙一般自带）。可选装 `curl_cffi`，Microsoft Rewards 脚本会用它伪装 TLS 指纹；NodeSeek 脚本没有它基本过不了 Cloudflare，要用就必须装。

推送：默认走面板「系统设置 → 通知设置」（需要青龙 2.18+）。这条推送失败时，改用青龙自带的 `notify.py`，它读「配置文件」里的 `TG_BOT_TOKEN`、`PUSH_KEY` 等变量。用环境变量 `QL_NOTIFY_MODE` 可以改推送方式：

| 值 | 推送方式 |
|---|---|
| `auto`（默认） | 先走面板通知设置，失败再走 notify.py |
| `system` | 只走面板通知设置 |
| `script` | 只走 notify.py |
| `off` | 不推送，只打印到日志 |

## 环境变量

多个账号用换行或 `&&` 分隔。

### 百度网盘

| 变量 | 说明 |
|---|---|
| `BAIDUWP_COOKIE` | 浏览器登录 pan.baidu.com，按 F12 → 网络，复制任一请求的 `Cookie` 请求头。至少要有 `BDUSS` 和 `STOKEN`。也可以只填 `BDUSS` 的值。 |

### WorkBuddy

| 变量 | 说明 |
|---|---|
| `WB_TOKEN` | 每个账号一行 `AT:RT`，前面可加 `备注:`。在本地运行 `python tools/wb_token.py`，用短信验证码登录后获取。 |

- 脚本每次先用 RT 续期，新的 AT / RT 自动写回 `WB_TOKEN`（需要青龙 2.18+）。写回失败时，日志里会打印新值，请手动更新。
- 用短信登录拿凭据，可能会让其他设备上的 WorkBuddy 退出登录。
- 5.6.2 以后的客户端会加密本地登录文件，所以不要从客户端文件里复制 token，用 `tools/wb_token.py`。

### B站

| 变量 | 默认 | 说明 |
|---|---|---|
| `BILI_COOKIE` | 无 | 浏览器登录 bilibili.com，复制 `Cookie` 请求头。必须有 `SESSDATA` 和 `bili_jct`。 |
| `BILI_COIN_NUM` | `0` | 每天投币总数，0～5。默认不投币。 |
| `BILI_SILVER2COIN` | `false` | `true` 时用银瓜子换硬币。 |
| `BILI_VIP_PRIVILEGE` | `true` | 大会员时自动领取每月权益；`false` 关闭。 |

### 小黑盒

| 变量 | 默认 | 说明 |
|---|---|---|
| `XHH_COOKIE` | 无 | App 登录态 + 设备字段。获取方法见 [小黑盒 Cookie 获取指南](docs/xiaoheihe-cookie.md)。多个账号用换行或 `&&` 分隔。 |
| `XHH_HKEY_API` | `https://hkey.qcciii.com/hkey` | 计算 App 签名和加密上报的服务地址，见下面说明。 |
| `XHH_AUTO_TASKS` | `true` | `false` 时只签到，不做浏览/分享任务（兼容旧名 `XHH_SHARE_TASKS`）。 |

两条签名通道，脚本自动选：

- **App 通道**：签到真正能用的通道。Cookie 里有 `x_xhh_tokenid` + 设备字段时优先走它。
- **Web 通道**：签名在本地算，不需要第三方服务；读任务列表、查 H 币没问题，但**签到会被拒**（返回「非法的请求」）——小黑盒的签到只认 App 签名。

所以关键结论：**要有 App 登录态才能签到**。网页扫码拿到的 pkey 只在 Web 通道有效，拿去 App 通道会提示「请重新登录」。

签到之后，脚本会顺手做能自动完成的每日任务（App 通道，`XHH_AUTO_TASKS=false` 可关闭）：

| 任务 | 做法 |
|---|---|
| 前往首页浏览三篇帖子 | 拉信息流取 3 篇不同帖子，各上报 30 秒阅读时长 |
| 分享任意帖子到社交平台 | 拉信息流 → 上报 5 秒浏览时长 → 上报「点击分享」「分享成功」两个事件 |
| 分享游戏详情到社交平台 | 拉推荐游戏 → 上报分享事件 |
| 分享游戏评价到社交平台 | 拉推荐游戏 → 拉该游戏评论 → 上报分享事件 |

「参与主题创作活动」需要真的发帖子，脚本不做，日志里会留在「未完成」。上报走 `data.xiaoheihe.cn`，内容由 hkey 服务加密（`mode=report`）。

#### 获取 XHH_COOKIE

Cookie 必须是 **App 登录态**：网页扫码拿到的只能在 Web 通道读数据，签到会被拒。

三种获取方式（手机号登录 / 扫码 / 抓包）、字段说明、常见报错和排查步骤，都整理在单独的文档里：

👉 **[小黑盒 Cookie 获取指南](docs/xiaoheihe-cookie.md)**

拿到后直接填青龙环境变量 `XHH_COOKIE`。

#### 关于 hkey 签名服务

App 的 `hkey` 由客户端 native 代码计算，纯脚本算不了，所以脚本把 `heybox_id`、`imei`、接口路径、时间戳发给 hkey 服务（默认社区公共的 `hkey.qcciii.com`，可用 `XHH_HKEY_API` 替换）代算签名，**不发送 Cookie**。

- 公共服务的可用性没有保障，挂了会报「hkey 服务请求失败」。
- 对隐私敏感的话可以自己部署同协议的服务，或者接受「只能读、不能签到」。

### Microsoft Rewards（国区，cn.bing.com）

> Microsoft Rewards 条款禁止自动化，有封号风险，没兑换的积分会作废。

| 变量 | 默认 | 说明 |
|---|---|---|
| `MSR_COOKIE` | 无 | 用插件导出，见下面「获取 MSR_COOKIE」。多个账号用 `&&` 分隔。 |
| `MSR_REFRESH_TOKEN` | 无 | 可选，用于 App 签到、阅读和其他 App 活动。在本地运行 `python tools/msr_token.py` 获取。多账号按 `MSR_COOKIE` 的顺序排。微软返回新 token 时，脚本自动写回这个变量（需要青龙 2.18+）。 |
| `MSR_SEARCH` | `true` | `false` 时不做搜索。 |
| `MSR_SEARCH_DELAY` | `15` | 两次搜索之间的基准秒数，最小 5。实际等待在基准的 0.5～1.8 倍之间抖动。 |
| `MSR_READ_DELAY` | `3` | 两次阅读上报之间的秒数，最小 1。调大更接近真实阅读。 |
| `MSR_APP_PROMOTIONS` | `true` | `false` 时跳过签到、阅读以外的其他 App 活动。 |
| `MSR_STEALTH` | `true` | `false` 时关掉设备档案、请求头补全和节奏抖动，只发最简请求，用于排查「加了请求头反而报错」。 |
| `MSR_SEARCH_BUDGET` | `10` | 新版页面不返回搜索计数时，按余额模式最多试几次搜索。 |
| `MSR_IMPERSONATE` | `auto` | `off` 时即使装了 `curl_cffi` 也不做 TLS 指纹伪装。 |

#### 获取 MSR_COOKIE

rewards.bing.com 的会话 Cookie 很快过期，只复制请求头会一直报 `HTTP 401`。脚本需要 login.live.com 的登录态，过期后自动续登（和浏览器自动跳转一样）。

1. 安装浏览器插件 [Cookie-Editor](https://cookie-editor.com/)。
2. 登录微软账号，勾选「保持登录」。
3. 打开 https://rewards.bing.com ，点 Cookie-Editor → Export → JSON。
4. 打开 https://login.live.com/oauth20_desktop.srf （空白页是正常的），再导出一次 JSON。
5. 把两段 JSON 前后拼在一起，整段填进 `MSR_COOKIE`。

也支持 cookies.txt 格式（Get cookies.txt LOCALLY 等插件）和普通 `Cookie` 请求头。请求头没有 login.live.com 的部分，过期后不能自动续登。

搜索和每日活动的登录态不是一回事：搜索走上报接口，`/earn` 页面要的是**网页会话**。日志里出现「网页会话已失效（/earn 被跳到登录页）」时，说明 rewards.bing.com 那份 Cookie 过期了 —— 脚本会先试一次静默续登，续不上就跳过每日活动并说明原因，不会拿失效会话继续发上报。按上面的步骤重新导出一份即可。

新版 rewards.bing.com 换了登录方式：`/earn` 会 302 到微软登录页，最后由一个**自动提交表单**把 `code` POST 回 `rewards.bing.com/auth/callback`。浏览器会执行那段 JS，脚本不会，所以现在脚本自己补这一步（`login_callback`），补完 `/earn` 就是 200。旧的 `api/getuserinfo` 已经不再返回 JSON，余额改从页面读，搜索在没有计数时按余额模式跑。

**登录不上时先自检**，不用整条任务跑一遍：

```bash
python tools/msr_probe.py cookies.json     # 也可以 MSR_COOKIE='...' python tools/msr_probe.py
```

它会打出四块内容：关键 Cookie（`_U`、`RPSSecAuth`、`MSPAuth`、`_C_Auth` 等）是否齐全、`getuserinfo` 在 requests 和 curl_cffi 下分别返回什么、`/earn` 有没有被 302 到登录页、静默续登能不能走通，最后给一句结论。常见结果：

| 现象 | 含义 |
|---|---|
| `getuserinfo` 401 + 静默登录 `login_required` | 这组 Cookie 已经换不到登录态，必须重新登录后导出；**先别再跑脚本**，反复失败只会让风控更紧 |
| `MSPAuth` / `MSPProf` 的值是 `Disabled` | 新版流程常见现象：浏览器靠 Edge/MSA 的会话登录，不下发持久票据。**不影响脚本**，脚本会用 OAuth 回调自己补登录 |
| 关键 Cookie 缺 `RPSSecAuth` / `MSPAuth` / `_C_Auth` | 导出不全，多半只导了某一个域名 |
| requests 正常、curl_cffi 401 | 传输层差异，先 `MSR_IMPERSONATE=off` 跑一次对比 |
| 全部正常但脚本仍报错 | 把完整日志贴出来，多半是接口改版 |

一份能用的导出至少要有 `_U`、`RPSSecAuth`、`_C_Auth`，并且 `MSPAuth` 不是 `Disabled`。

另外，**浏览器的登录态和脚本不要长期共用**。微软会轮换 `RPSSecAuth` 这类令牌，两边同时用一个快照时，服务端可能把旧的判为失效，表现就是浏览器和脚本一起被登出。跑脚本时先关掉 rewards 页面，重新导出前也刷新一次页面。

任务：

| 任务 | 说明 |
|---|---|
| 电脑 / 手机搜索 | 在 cn.bing.com 搜百度、微博热搜词；检查上报结果，按面板计数器搜到满分为止 |
| 每日活动 | 点击类活动直接完成；测验类可能完成不了；后置状态无法读取时标为「未确认」，不虚报完成 |
| App 签到、阅读赚积分 | 需要 `MSR_REFRESH_TOKEN`；阅读按实际进度和余额停止，不再固定空跑 12 次 |
| 其他 App 活动 | 自动处理面板中可上报的 `sapphire` 活动，可用 `MSR_APP_PROMOTIONS=false` 关闭 |

单个任务失败不会中断同一账号的其他任务；网页 Cookie 失效时仍会尝试独立的 App 任务。

#### 关于风控

脚本不做硬绕过，只是让请求别那么像脚本：

- **设备档案**：每个账号固定一套 UA + Client Hints（`sec-ch-ua*`）+ 平台，大版本统一，电脑端和手机端共用同一个 Edge 大版本，像一个真人的两台设备。档案按账号的 `MUID` 派生、按月轮换，同一个号每天跑看到的都是同一台设备，不会今天 Windows 明天换安卓。版本号跟着当前稳定版走，落后一两个大版本最像自动更新过的机器。
- **请求头**：补上浏览器会发的 `sec-fetch-*`、`Upgrade-Insecure-Requests`、`Accept`、`Accept-Language`；`Accept-Encoding` 只声明 requests 实际能解的编码，免得拿到 br 解不开。`Origin` 只在 POST 上带（Fetch 规范如此），GET 带上反而露馅。
- **Referer 链**：搜索前先开一次 Bing 首页，让站点自己下发 `MUID`、`SRCHHPGUSR` 等 Cookie；之后的搜索从上一页点过来，偶尔当作新开标签页。
- **节奏**：间隔按对数正态抖动，不再固定 15 秒；中间小概率插入几十秒空档；多账号之间至少隔一分钟，每个账号开局也有随机延迟；电脑端和手机端的先后顺序每次随机。
- **及时收手**：遇到 403 / 418 / 429 或验证码页面立刻停掉该项，不重试、不加压。被盯上还继续刷，比少拿几分危险得多。

想要更强的传输层伪装，可以在青龙「依赖管理 → Python3」里装 `curl_cffi`，脚本会自动改用它走 Chrome 的 TLS/HTTP2 指纹；没装就用 requests，行为不变。

需要说明的是：TLS 指纹（JA3/JA4）由客户端决定，只靠请求头改不了；装了 `curl_cffi` 才有改善。以上都只是降低特征，**不能保证不被风控**，介意风险就别跑。

### NodeSeek / DeepFlood

| 变量 | 默认 | 说明 |
|---|---|---|
| `NS_COOKIE` | 无 | 浏览器登录 nodeseek.com，用 Cookie-Editor 导出 Header String 或 JSON，或者在 F12 → 网络里复制 `Cookie` 请求头。多个账号用换行或 `&&` 分隔（JSON 之间只能用 `&&`）。 |
| `DF_COOKIE` | 无 | deepflood.com 的 Cookie，可选，格式同上。 |
| `NS_UA` | 无 | 导出 Cookie 的那个浏览器的 User-Agent，强烈建议填：在那个浏览器的控制台运行 `navigator.userAgent`，复制结果。所有账号共用。 |
| `NS_RANDOM` | `true` | `true` 随机鸡腿，`false` 固定 5 个。 |

- 需要在「依赖管理 → Python3」里装 `curl_cffi`。脚本按 `NS_UA` 选 Chrome / Edge / Firefox / Safari 的 TLS 指纹，并补齐一致的 `Sec-Ch-Ua` 请求头。
- Cookie 里的 `cf_clearance` 绑定导出时的 IP 和 UA。青龙所在机器和浏览器走同一个出口 IP（比如都在家里内网）时最稳；IP 不同就可能被拦，日志会显示「被 Cloudflare 拦截」。
- 签到 0 点刷新，默认定时 00:07。当天已签到的账号记为成功（☑️）。

## 安全

- Cookie 等于账号登录态。只放在青龙的环境变量里，不要提交到这个仓库。
- 脚本调用的都是非官方接口。接口改动后脚本会报错，看青龙日志里的返回内容排查。

## 本地测试

```bash
pip install -r requirements.txt
BAIDUWP_COOKIE="..." python checkin_baiduwp.py
```

## 致谢

接口参考 [Sitoi/dailycheckin](https://github.com/Sitoi/dailycheckin)（MIT）、[NodeSeek Pro 油猴脚本](https://greasyfork.org/zh-CN/scripts/567109-nodeseek-pro)（GPL-3.0）、[SocialSisterYi/bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect) 和 [yowiv08/heybox](https://github.com/yowiv08/heybox)。小黑盒 Web 端签名算法来自 [foxlesbiao/xiaoheihe-mcp](https://github.com/foxlesbiao/xiaoheihe-mcp) 和公开的逆向分析。
