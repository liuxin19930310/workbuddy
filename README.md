# WorkBuddy Buddy 加油站 · 自动签到

用 GitHub Actions 每天定时调用 WorkBuddy 官方签到接口领取积分。**不依赖本机是否开机** —— 电脑关机、出差、假期都能照常签到。

这是为此目的单独建立的仓库，只承载这一个自动化任务。

---

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

GitHub Actions 的 cron 使用 **UTC 时间**：

```yaml
- cron: '0 1,4,7,10,13 * * *'   # 北京时间 09:00 / 12:00 / 15:00 / 18:00 / 21:00
```

多时点触发 + 幂等设计：任一时刻成功领取即可，不会重复领分，同时规避 GitHub 定时任务的偶发延迟。

---

## 四、维护须知

1. **令牌需定期更换**（主要维护成本）
   accessToken 60 天、refreshToken 90 天，且本机客户端每次启动都会轮换令牌，所以这里存的快照迟早失效。
   → **建议每 1~2 个月重跑一次 `get-token.ps1` 更新 `WB_TOKEN`**。到期后脚本会输出 `status=error / 令牌已失效`，不会被误报成签到成功。

2. **仓库保活已内置**
   workflow 里带了「每月心跳提交」步骤：每月首次运行时自动提交一次 `.keepalive/last-heartbeat.txt`，使仓库始终有活动，避免 GitHub 在 60 天无提交时自动停用定时任务。该步骤 `continue-on-error`，即使推送失败也不影响签到结果。

3. **额度**
   私有仓 Free 账户约 2000 分钟/月；本任务每次约 1 分钟、每天 5 次，约 150 分钟/月。

4. **合规**
   调用的是你自己的账号接口，属个人自动化。若官方调整接口或规则，以官方说明为准；接口路径若变化，只需修改 `checkin.py` 顶部常量。

---

## 五、本机使用（可选）

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

## 文件说明

| 文件 | 作用 |
|---|---|
| `checkin.py` | 签到主脚本。零第三方依赖（仅标准库），幂等、令牌全脱敏、可选微信推送；本地/CI 双模式 |
| `.github/workflows/checkin.yml` | 定时任务定义（UTC cron、手动触发、并发保护、月度心跳保活） |
| `get-token.ps1` | 提取本机 accessToken 到剪贴板，只打印脱敏预览与到期时间 |
