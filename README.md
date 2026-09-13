# WorkBuddy 每日自动任务集

用 GitHub Actions 每天定时调用 WorkBuddy 官方接口，**不依赖本机是否开机** —— 电脑关机、出差、假期都能照常跑。

本仓库承载两个独立自动化，共用同一个令牌 Secret：

| 自动化 | 脚本 | 工作流 | 做什么 |
|---|---|---|---|
| **Buddy 加油站签到** | `checkin.py` | `checkin.yml` | 每日领取签到积分（+100，连签有额外奖励） |
| **派猫猫旅行** | `travel.py` | `travel.yml` | 派出猫猫旅行 → 归来后自动领取 5~10 积分（每日闭环） |

> ## ⚠️ 本仓库必须保持 public，不要改回私有
>
> **原因**：免费个人账号下，**私有仓库的 `schedule` 定时事件不会触发**（社区实证；官方文档只写了免费计划私有仓 2000 分钟/月，未记载这条限制）。
>
> **判定签名**（一眼可辨，2026-09-12 实测于本仓）：
> - Actions 页只有手动运行记录，**零条 `schedule` 运行**；
> - 工作流详情页横幅只写 *"This workflow has a `workflow_dispatch` event trigger."*，**完全不提 `schedule`**；
> - 而配置怎么查都没问题：令牌有效、默认分支正确、YAML 无 TAB、`on: schedule` 齐全、手动运行能成功。
>
> 私有期间，08:15 与 09:00 两个定时点均未产生任何运行记录；改为 public 后定时恢复。
>
> **若确实必须私有**（两条替代路径）：
> 1. 升级 GitHub Pro（$4/月）—— 官方支持私有仓定时；
> 2. 外部定时器（cron-job.org 等）带 fine-grained PAT（`Actions: write`）调 `workflow_dispatch` 接口：
>    `curl -X POST -H "Authorization: Bearer <PAT>" https://api.github.com/repos/<owner>/<repo>/actions/workflows/checkin.yml/dispatches -d '{"ref":"main"}'`
>
> 附带好处：public 仓的标准 runner Actions 分钟**免费且不限量**（私有仓才有 2000 分钟/月上限）。

---

# 第一部分：Buddy 加油站签到

## 一、原理（已在本机实测验证）

签到本质是一次带登录令牌的 HTTP 请求，不需要 GUI。实测结果（2026-09-11，客户端 v5.3.x）：

| 用途 | 请求 | 实测返回 |
|---|---|---|
| 查询状态 | `POST https://www.codebuddy.cn/v2/billing/meter/checkin-activity-status` | `code=0`，`data.active=true`、`streak_days=10`、`today_credit=100` |
| 领取签到 | `POST https://www.codebuddy.cn/v2/billing/meter/daily-checkin` | 今日已领：HTTP 400 + `code=10001`「今天已签到，请明天再来」（**幂等**，可安全重复调用） |
| 鉴权 | 请求头 `Authorization: Bearer <accessToken>` | 有效 |
| 请求体 | `{}` | — |

**两个容易踩的坑（网上流传的错误写法，实测不可用）**：

- `copilot.tencent.com` 域名 → 不可用；当前正确域名是 **`www.codebuddy.cn`**。
- `/v2/billing/meter/checkin-status` → 返回 `code=0` 但数据全空（`active=false`）；必须用 **`checkin-activity-status`**。

模拟鼠标点击 GUI 按钮这条路走不通：Electron 会过滤 `isTrusted=false` 的合成点击事件。

### 令牌来源

```
Windows: %LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info
macOS:   ~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info
```

明文 JSON，只读、不外传：

| 字段 | 说明 |
|---|---|
| `auth.accessToken` | JWT，有效期 **60 天**（`expiresIn = 5184000` 秒） |
| `auth.refreshToken` | 有效期 **90 天**（`refreshExpiresIn = 7776000` 秒） |
| `auth.domain` | `www.codebuddy.cn` |

---

## 二、部署步骤

### ✅ 已完成
- 仓库初始化，包含 `checkin.py`、`.github/workflows/checkin.yml`、`get-token.ps1`

### ⬜ 待你手动完成（2 步）

**第 1 步：准备令牌**

本机登录 WorkBuddy 桌面端，在本仓库目录下运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\get-token.ps1
```

脚本会把 accessToken 复制到剪贴板，控制台只打印脱敏预览与到期时间，**不会显示完整令牌**。

**第 2 步：配置 Secret**

仓库 `Settings → Secrets and variables → Actions → New repository secret`

| 名称 | 值 | 说明 |
|---|---|---|
| `WB_TOKEN` | 上一步剪贴板的 accessToken | **必填，名称必须完全一致** |
| `SERVERCHAN_KEY` | Server 酱 SendKey | 可选；填了会推微信通知（不需要就别建） |

> 这一步只能你在网页上操作：GitHub Secret 无法通过 SSH 或命令行写入。

**第 3 步：验证**

`Actions → WorkBuddy Daily Checkin → Run workflow` 手动触发一次，看日志 JSON：

- `action=claimed` → 领取成功
- `action=skip_already_signed` → 今日已领，幂等跳过（正常）

---

## 三、调度说明

### 主调度：外部定时器（时间可控）

GitHub 原生 `schedule` 是 best-effort：实测延迟从 2 分钟到 **3 小时 08 分**不等，还出现过**整次丢弃**。
所以真正负责「准时」的是一个外部定时器（cron-job.org），它在固定时刻调用 GitHub 的
`workflow_dispatch` 接口来触发本工作流：

| 时间（北京） | 动作 |
|---|---|
| **00:05** | 触发签到 —— 跨过零点即可领取，最稳，不会漏签 |
| **08:00** | 触发猫猫出发 |
| **12:30** | 触发猫猫归来领取（最慢的 4 小时行程也必然已归来） |

配置方法（PAT 创建 / job 填写 / 验证）见 **[EXTERNAL-CRON.md](EXTERNAL-CRON.md)**。

### 兜底：GitHub 原生 schedule

GitHub Actions 的 cron 使用 **UTC 时间**：

```yaml
- cron: '0 1,4,7,10,13 * * *'   # 北京时间 09:00 / 12:00 / 15:00 / 18:00 / 21:00
```

多时点触发 + 幂等设计：任一时刻成功领取即可，不会重复领分。
**外部定时器失效时，由它保证「当天仍然领得到」** —— 两层调度互不冲突，同时触发也只领一次。

---

## 四、维护须知

1. **令牌需定期更换**（主要维护成本）
   accessToken 60 天、refreshToken 90 天，且本机客户端每次启动都会轮换令牌，所以这里存的快照迟早失效。
   → **建议每 1~2 个月重跑一次 `get-token.ps1` 更新 `WB_TOKEN`**。到期后脚本会输出 `status=error / 令牌已失效`，不会被误报成签到成功；配了 `SERVERCHAN_KEY` 时还会同时推微信提醒你。

2. **仓库保活已内置**
   两个 workflow 末尾都有「每日运行戳」步骤：把当天第一笔运行写进 `.keepalive/last-run-*.txt` 并提交，
   既让仓库始终有活动（避免 GitHub 在 60 天无提交时自动停用定时任务），
   又留下了一份**不需要任何 API 就能读的运行账本**。该步骤 `continue-on-error`，推送失败也不影响领取结果。

3. **外部定时器的 PAT 也会过期**
   cron-job.org 用的 GitHub PAT 最长有效期 1 年，到期后 6 个 job 会全部返回 401。
   重建令牌并更新 job 的 `Authorization` 头即可，步骤见 [EXTERNAL-CRON.md](EXTERNAL-CRON.md) 第六节。

4. **Actions 额度**
   本仓为 **public**，标准 runner 的 Actions 分钟**免费且不限量**（私有仓才有 2000 分钟/月上限）。本任务约 150 分钟/月，压力可忽略。

5. **合规**
   调用的是你自己的账号接口，属个人自动化。若官方调整接口或规则，以官方说明为准；接口路径若变化，只需修改 `checkin.py` 顶部常量。

---

## 五、微信推送（Server 酱，可选但建议）

**为什么建议配**：令牌 60 天后必然失效，没有推送的话你不会察觉，只会发现积分断了。配了之后"失败也会主动告诉你"。

### 配置

1. 打开 [sct.ftqq.com](https://sct.ftqq.com)，微信扫码登录，复制页面上的 **SendKey**（形如 `SCT` 开头的一串）。
2. 仓库 `Settings → Secrets and variables → Actions → New repository secret`，名称 **`SERVERCHAN_KEY`**，值填 SendKey。

### 推送策略（重要：按需推送，不做骚扰）

推送级别由环境变量 **`PUSH_LEVEL`** 控制，两个自动化一致：

| `PUSH_LEVEL` | 行为 | 每天条数 |
|---|---|---|
| **`all`**（当前默认） | **每次巡检都推**，含「今日已签到」这类无动作结果 | 签到最多 5 条 |
| `action` | 只在领取成功 / 出错时推（安静模式） | 最多 1 条 |

切换方法：编辑 `.github/workflows/checkin.yml`（或 `travel.yml`）里的 `PUSH_LEVEL: all` 改成 `action`，提交即生效。

`PUSH_LEVEL: all` 下你会收到的标题：

| 场景 | 标题 |
|---|---|
| 领取成功 | `签到成功 +100 积分` |
| 巡检（今日已签） | `签到巡检 · 今日已签到` |
| 出错 | `签到异常，需要处理` 或 `猫猫旅行异常，需要处理` |

正文示例：

```
时间：2026-09-12 09:00:00 +08
结果：领取成功
本次积分：+100
连续签到：11 天
```

> 注意：`SERVERCHAN_KEY` 未配置时，`notify()` 直接返回，脚本行为完全不受影响。

### 当场验证配置（不用等到明天）

手动触发的运行本身就会推送（`PUSH_LEVEL=all` 时），所以直接：

1. `Actions → WorkBuddy Daily Checkin`（或 `WorkBuddy Cat Travel`）`→ Run workflow`
2. 点 `Run workflow`
3. 几秒后微信应收到一条巡检结果

收到 ⇒ `SERVERCHAN_KEY` 配置正确、通道可用。没收到 ⇒ SendKey 填错或未创建该 Secret（脚本对推送失败是静默的，不会让 job 变红）。

若已把 `PUSH_LEVEL` 改成 `action`（安静模式），可勾选 **`force_notify`** 强制推送一条（标题带「（测试）」前缀）来验证。

推荐的安全观察方式：Cat Travel 支持 `dry_run = true`，只查状态不落任何写操作，也会产生一条巡检推送。

---

## 六、本机使用（可选）

`checkin.py` 同时兼容本机运行 —— 不设置 `WB_TOKEN` 时会自动读取本机登录态文件：

```bash
python checkin.py
```

实测输出：

```json
{"status": "ok", "action": "skip_already_signed", "streak_days": 10, "today_credit": 100, "msg": "今日已签到，无需重复领取"}
```

因此同一份脚本既能放在 GitHub Actions 跑，也能在本机（含 Windows 计划任务）跑。

---

# 第二部分：派猫猫旅行自动闭环

## 一、玩法与自动化思路

「成长计划 → Buddy → 派猫猫旅行」：把 Buddy 派去一个地点旅行 **1~4 小时**，归来可领 **5~10 积分**，**每日限一次**。地点：咖啡馆 / 商场店铺 / 健身房 / 古镇客栈。

手动玩的问题是「要在固定时间窗口回来领」—— 忘了当天额度就作废。所以做成定时巡检：

```
GET /status 读服务端状态
    state=arrived            → POST /claim   领取（只领一次）
    state=traveling          → 跳过          绝不重复派出
    daily_limit_reached=true → 跳过          今日额度已用尽
    state=idle（额度未用）    → POST /depart  派出旅行
```

**幂等由服务端状态保证**：脚本自身无状态，重复执行、定时器抖动都不会重复派或重复领。每次运行只做一次「读状态 → 定动作」，不做后台长时间轮询。

## 二、接口（已在本机实测，与网上流传的说法有出入）

| 项 | 实测结论 |
|---|---|
| 域名 | `https://www.workbuddy.cn` |
| 路径前缀 | `/activity/growth/buddy/travel/` |
| 端点 | `GET /status`、`GET /config`、`POST /depart`、`POST /claim` |
| 鉴权 | **桌面端 Bearer 令牌可用**（无需浏览器 Cookie） |
| 关键字段 | `state`（idle/traveling/arrived）、`daily_limit_reached`、`record_id`、`arrive_at`、`reward_credit`、`location.name`、`server_now` |
| depart 参数 | **`location_id`**（传错值时服务端明确回 `location not available`，字段名由此确认） |

> ⚠️ 与网文说法的出入：网上教程称「必须用浏览器 Cookie，且路径不能带 /v2/，否则 401」。本机实测：**Bearer 令牌可用**，且**带不带 `/v2/` 都返回 200**（网文的 401 是因为当时没有有效凭据，与路径无关）。因此这里复用签到那一个令牌，无需维护易过期的 Cookie，也就能放进 GitHub Actions。

## 三、调度说明

```yaml
- cron: '15 0 * * *'   # 北京 08:15  派出（若额度未用）
- cron: '30 4 * * *'   # 北京 12:30  巡检领取
- cron: '45 8 * * *'   # 北京 16:45  巡检领取
- cron: '0 13 * * *'   # 北京 21:00  兜底
```

因为旅行耗时 1~4 小时是随机的，单点定时会漏领；四个时点保证「无论随机到几小时，当天都能被领取」。

## 四、手动操作

`Actions → WorkBuddy Cat Travel → Run workflow`，可勾选：

- `dry_run = true`：只查状态，不派出也不领取（安全的观察模式）
- `location = 1/2/3/4`：指定地点，留 `0` 为随机

本机调试：

```bash
python travel.py --dry-run      # 只看状态
python travel.py                # 跑一轮
python travel.py --location 1   # 指定咖啡馆
```

## 五、推送策略

`PUSH_LEVEL` 与签到共用同一套机制，**默认 `all`：每次巡检都推送**。

| 场景 | `all`（默认） | `action`（安静模式） | 标题 |
|---|---|---|---|
| 派出成功 | ✅ | ✅ | `猫猫已出发 · 咖啡馆` |
| 领取成功 | ✅ | ✅ | `猫猫旅行归来 +10 积分` |
| 巡检：旅行中 | ✅ | ❌ | `猫猫巡检 · 旅行中（还有 1 小时 51 分）` |
| 巡检：今日已完成 | ✅ | ❌ | `猫猫巡检 · 今日已完成` |
| 出错 | ✅ | ✅ | `猫猫旅行异常，需要处理` |

`all` 模式下一天最多 4 条（对应 4 个巡检时点）；`action` 模式正常一天 2 条。

旅行中的推送会带上**剩余时间**（用服务端时间计算，不吃本机时钟偏差），正文示例：

```
时间：2026-09-12 00:08:12 +08
状态：traveling
动作：旅行中，等待归来
地点：咖啡馆
剩余时间：约 1 小时 51 分
预计归来：2026-09-12 02:00:06 +08
```

---

## 文件说明

| 文件 | 作用 |
|---|---|
| `checkin.py` | 签到主脚本。零第三方依赖（仅标准库），幂等、令牌全脱敏、`PUSH_LEVEL` 可调推送级别；本地/CI 双模式 |
| `travel.py` | 派猫猫旅行闭环脚本。状态机 + 幂等，纯标准库，令牌全脱敏，`PUSH_LEVEL` 可调；本地/CI 双模式 |
| `.github/workflows/checkin.yml` | 签到任务（外部定时器 + 原生 cron 兜底、手动触发、并发保护、每日运行戳、Node 24 版 action） |
| `.github/workflows/travel.yml` | 猫猫旅行任务（外部定时器 + 4 个时点兜底巡检、支持 dry_run 与指定地点） |
| `EXTERNAL-CRON.md` | **外部定时器部署手册**：cron-job.org 配置、GitHub PAT 创建、验证方法与维护须知 |
| `get-token.ps1` | 提取本机 accessToken 到剪贴板，只打印脱敏预览与到期时间 |

> 两个自动化共用 Secret：`WB_TOKEN`（令牌）、`SERVERCHAN_KEY`（可选，微信推送）。令牌过期后两个都会失效，此时重跑 `get-token.ps1` 更新 `WB_TOKEN` 即可。
>
> 推送想更安静：把两个 workflow 里的 `PUSH_LEVEL: action` 改成 `off`（彻底静默）；改成 `all` 则每次巡检都推。
