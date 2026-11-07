# ql-scripts

自用的青龙面板签到脚本。

| 脚本 | 任务 | 默认定时 | 环境变量 |
|---|---|---|---|
| `checkin_baiduwp.py` | 百度网盘会员签到 + 每日答题 | 每天 08:15 | `BAIDUWP_COOKIE` |
| `checkin_quark.py` | 夸克网盘签到领容量 | 每天 08:25 | `QUARK_PARAM` |
| `checkin_workbuddy.py` | WorkBuddy「Buddy 加油站」每日签到领积分 | 每天 09:10 | `WB_TOKEN` |
| `checkin_bilibili.py` | B站漫画签到、直播签到、观看、分享，可选投币 | 每天 08:30 | `BILI_COOKIE` 等 |
| `checkin_msrewards.py` | Microsoft Rewards 国区：搜索、每日活动、App 签到、阅读 | 每天 09:20 | `MSR_COOKIE` 等 |

`qlutils.py` 是公共模块，不是任务。

## 拉取到青龙

在青龙「订阅管理」里新建订阅，或者在任务里执行：

```bash
ql repo https://github.com/f1shQAQ/ql-scripts.git "checkin_" "" "qlutils" "main" "py"
```

参数依次是：仓库地址、白名单（只把 `checkin_` 开头的文件建成任务）、黑名单、依赖文件、分支、文件后缀。

依赖：在「依赖管理 → Python3」里添加 `requests`（青龙一般自带）。

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

### 夸克网盘

签到只能用移动端接口，网页 Cookie 签不了，需要手机抓包一次。

| 变量 | 说明 |
|---|---|
| `QUARK_PARAM` | 抓包拿到的 drive-m.quark.cn 请求 URL，里面要有 `kps`、`sign`、`vcode`。也可以写成 `kps=...; sign=...; vcode=...`，前面可加 `user=备注;`。 |

获取步骤：

1. 手机装抓包工具（iOS 用 Stream 或 Reqable，Android 用 Reqable），按工具提示安装并信任证书。
2. 打开抓包，再打开夸克 App →「我的」→「签到领空间」。
3. 在抓包记录里找任意一个 `drive-m.quark.cn` 的请求，例如 `/1/clouddrive/capacity/growth/info`。
4. 复制整段 URL，填进 `QUARK_PARAM`。

参数的有效期没有官方说明。脚本报「参数可能已失效」时，重新抓一次。

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

### Microsoft Rewards（国区，cn.bing.com）

> Microsoft Rewards 条款禁止自动化，有封号风险，没兑换的积分会作废。脚本不含防检测代码。

| 变量 | 默认 | 说明 |
|---|---|---|
| `MSR_COOKIE` | 无 | 用插件导出，见下面「获取 MSR_COOKIE」。多个账号用 `&&` 分隔。 |
| `MSR_REFRESH_TOKEN` | 无 | 可选，用于 App 签到和阅读。在本地运行 `python tools/msr_token.py` 获取。多账号按 `MSR_COOKIE` 的顺序排。微软返回新 token 时，脚本自动写回这个变量（需要青龙 2.18+）。 |
| `MSR_SEARCH` | `true` | `false` 时不做搜索。 |
| `MSR_SEARCH_DELAY` | `15` | 两次搜索之间的秒数，最小 5。 |

#### 获取 MSR_COOKIE

rewards.bing.com 的会话 Cookie 很快过期，只复制请求头会一直报 `HTTP 401`。脚本需要 login.live.com 的登录态，过期后自动续登（和浏览器自动跳转一样）。

1. 安装浏览器插件 [Cookie-Editor](https://cookie-editor.com/)。
2. 登录微软账号，勾选「保持登录」。
3. 打开 https://rewards.bing.com ，点 Cookie-Editor → Export → JSON。
4. 打开 https://login.live.com/oauth20_desktop.srf （空白页是正常的），再导出一次 JSON。
5. 把两段 JSON 前后拼在一起，整段填进 `MSR_COOKIE`。

也支持 cookies.txt 格式（Get cookies.txt LOCALLY 等插件）和普通 `Cookie` 请求头。请求头没有 login.live.com 的部分，过期后不能自动续登。

任务：

| 任务 | 说明 |
|---|---|
| 电脑 / 手机搜索 | 在 cn.bing.com 搜百度、微博热搜词，按面板计数器搜到满分为止 |
| 每日活动 | 点击类活动直接完成；测验类可能完成不了 |
| App 签到、阅读赚积分 | 需要 `MSR_REFRESH_TOKEN` |

## 安全

- Cookie 等于账号登录态。只放在青龙的环境变量里，不要提交到这个仓库。
- 脚本调用的都是非官方接口。接口改动后脚本会报错，看青龙日志里的返回内容排查。

## 本地测试

```bash
pip install -r requirements.txt
BAIDUWP_COOKIE="..." python checkin_baiduwp.py
```

## 致谢

接口参考 [Sitoi/dailycheckin](https://github.com/Sitoi/dailycheckin)（MIT）和 [SocialSisterYi/bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect)。
