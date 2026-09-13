# 外部定时器：让执行时间变得可控

本文件说明如何用 **cron-job.org** 在精确时刻触发本仓库的工作流，替代 GitHub 原生
`schedule` 的「尽力而为」调度。

> 文中的仓库名以 `liuxin19930310/workbuddy` 与 `liuxin19930310/workbuddy_867177378` 为例；
> 部署到新仓库时把这两处替换成实际仓库即可，其余步骤完全一致。

---

## 一、为什么需要它

GitHub Actions 的 `schedule` 官方定位就是 best-effort（尽力而为）：负载高时会延迟，
必要时会**整次丢弃**。本仓库的实测数据（2026-09-12 ~ 09-13）：

| 计划时间（北京） | 实际执行 | 偏差 |
|---|---|---|
| 09-12 12:30 | 12:31:59 | +2 分钟 |
| 09-12 08:15 | 10:18 | **+2 小时 03 分** |
| 09-12 21:00 | 09-13 00:08 | **+3 小时 08 分** |
| 09-13 08:15 | 未执行 | **整次丢弃** |

结论：**当天一定会领到**（多时点 + 幂等保证了这点），但**具体几点完全不可控**。
如果你希望「签到就是 00:05 完成、猫猫就是 08:00 出发」，就必须换一个可靠的调度源。

## 二、架构

```
cron-job.org（精确到分钟，免费）
  ├─ 00:05 北京 → POST dispatch checkin.yml
  ├─ 08:00 北京 → POST dispatch travel.yml   （猫猫出发）
  └─ 12:30 北京 → POST dispatch travel.yml   （猫猫归来领取）
            ↓  Authorization: Bearer <细粒度 PAT>
GitHub Actions（workflow_dispatch，几秒内启动）
            ↓
checkin.py / travel.py（幂等，重复触发不会重复领分）
            ↓
Server 酱 / Bark 推送
```

**GitHub 原生 `schedule` 保持不动，作为兜底。** 外部调度器挂掉时它仍能保证「当天领到」，
两边同时触发也不会重复领分 —— 幂等由服务端状态保证。

---

## 三、步骤 1：创建 PAT

外部调度器需要一个能调用 GitHub API 的令牌。**推荐细粒度（fine-grained）**，因为它可以
把权限压到最小。

### 方案 A：细粒度 PAT（推荐）

1. 打开 <https://github.com/settings/personal-access-tokens/new>
2. **Token name**：`cron-job-org-workbuddy`
3. **Expiration**：选最长（1 年）。到期需重建 —— 建议记进日历
4. **Resource owner**：`liuxin19930310`
5. **Repository access** → `Only select repositories` → 勾选：
   - `workbuddy`
   - `workbuddy_867177378`
6. **Permissions** → `Repository permissions` → 找到 **Actions** → 设为 **Read and write**
   （`Metadata` 会自动带上 Read-only，这是必需的，不用管）
7. `Generate token` → 复制 `github_pat_...`（**只显示这一次**）

### 方案 B：Classic PAT（方案 A 报 403 时用）

社区有过细粒度令牌调用 dispatch 接口返回 `403 Resource not accessible by personal access
token` 的案例（组织仓更常见，个人仓一般正常）。若遇到就走这条路：

1. <https://github.com/settings/tokens/new>
2. **Note**：`cron-job-org-workbuddy`
3. **Expiration**：1 年
4. **Select scopes**：只勾 **`public_repo`**（两个仓都是公开仓，够用；`repo` 权限过大，不必勾）
5. `Generate token` → 复制 `ghp_...`

> 权限对比：细粒度只给「触发/取消工作流」，**改不了代码、读不到 Secret**；
> classic 的 `public_repo` 可以改写你所有公开仓的内容，权限明显更大。
> 所以优先用方案 A。

---

## 四、步骤 2：在 cron-job.org 建 6 个 job

注册 <https://cron-job.org>（免费，邮箱验证后可用），然后逐个创建。

### 每个 job 的公共配置

| 字段 | 值 |
|---|---|
| Request method | **POST** |
| Header 1 | `Accept` → `application/vnd.github+json` |
| Header 2 | `Authorization` → `Bearer <你的 PAT>` |
| Header 3 | `Content-Type` → `application/json` |
| Request body | `{"ref":"main"}` |

> cron-job.org 会忽略 `User-Agent` 和 `Connection` 头，属正常；GitHub 只要求「有」User-Agent，
> 它会自带一个，不影响。

> **时区**：job 编辑器里如果能选时区，选 **Asia/Shanghai**，用下表「北京时刻」列；
> 找不到时区选择就按 **UTC** 列填（两列等价）。

### 6 个 job 一览

| # | Title（建议） | URL | 计划（北京） | 计划（UTC） |
|---|---|---|---|---|
| 1 | `WB 签到 · 旧号` | `https://api.github.com/repos/liuxin19930310/workbuddy/actions/workflows/checkin.yml/dispatches` | 每天 00:05 | `5 16 * * *` |
| 2 | `WB 猫猫出发 · 旧号` | `https://api.github.com/repos/liuxin19930310/workbuddy/actions/workflows/travel.yml/dispatches` | 每天 08:00 | `0 0 * * *` |
| 3 | `WB 猫猫领取 · 旧号` | 同 #2 | 每天 12:30 | `30 4 * * *` |
| 4 | `WB 签到 · 新号` | `https://api.github.com/repos/liuxin19930310/workbuddy_867177378/actions/workflows/checkin.yml/dispatches` | 每天 00:05 | `5 16 * * *` |
| 5 | `WB 猫猫出发 · 新号` | `https://api.github.com/repos/liuxin19930310/workbuddy_867177378/actions/workflows/travel.yml/dispatches` | 每天 08:00 | `0 0 * * *` |
| 6 | `WB 猫猫领取 · 新号` | 同 #5 | 每天 12:30 | `30 4 * * *` |

对应 cron 表达式（北京时区下）：

```
5 0 * * *      # 签到
0 8 * * *      # 猫猫出发
30 12 * * *    # 猫猫领取
```

### 为什么猫猫只要两个时点

猫猫每天限一次：08:00 派出后 1~4 小时随机归来，**12:30 那次即使最慢的 4 小时行程也必然已归来**，
所以一次领取就够。若 08:00 那次派出失败，GitHub 原生的 08:15 / 12:30 / 16:45 / 21:00 会兜底。

---

## 五、步骤 3：验证（3 分钟）

1. **cron-job.org 里点 `Test run`** —— 看响应码：
   - `204` 或 `200` = ✅ 成功
   - `401` = PAT 无效或已过期
   - `403` = 权限不足（改用方案 B 的 classic PAT）
   - `404` = 仓库名或 workflow 文件名写错
2. **GitHub 仓库 → Actions 页** —— 应出现一条 Event 为 `Manually run` 的运行，
   时间与你点 Test run 的时刻吻合。（注意：用 API 触发的运行在页面上也显示为 "Manually run"，
   无法与真人手点区分，看时间即可。）
3. **看账本** —— 仓库里 `.keepalive/last-run-checkin.txt` / `last-run-travel.txt` 会更新为：

   ```
   2026-09-14 00:05:12 +0800 checkin event=workflow_dispatch action=claimed
   ```

   `event=workflow_dispatch` 表示这次是外部调度器触发的（原生定时是 `event=schedule`），
   `action=claimed` 表示真的领到了分。**这一行就是「外部定时器生效」的硬证据。**

---

## 六、日常维护

| 事项 | 周期 | 怎么处理 |
|---|---|---|
| **PAT 到期** | 最长 1 年 | 重建令牌，逐个更新 cron-job.org 里 6 个 job 的 `Authorization` 头 |
| **`WB_TOKEN` 到期** | 约 60 天 | 跑 `get-token.ps1`，更新仓库 Secret `WB_TOKEN`（与外部调度器无关，但两者都过期就全停） |
| **job 被自动停用** | 连续失败 25 次后 | cron-job.org 会自动禁用该 job；去后台看执行历史与失败原因 |
| **失败没被察觉** | —— | 建议在 cron-job.org 的 Notifications 里开启失败邮件提醒 |

> 三个时点都在「几分钟」量级的容差内，即使 cron-job.org 偶尔延迟几分钟也不影响结果。

## 七、安全说明

- PAT 会以请求头形式保存在 cron-job.org 的 job 配置里（其服务器在德国，配置在后台可见）。
  这是本方案唯一的额外暴露面。
- 缓解措施：**权限压到最小**（细粒度只给 `Actions: Read and write`、只勾 2 个仓）——
  即使泄露，攻击者也只能触发/取消工作流，**无法读取 Secret、无法修改代码**。
- 随时可在 GitHub 一键撤销该 PAT（`Settings → Developer settings → Personal access tokens`），
  撤销后 cron-job.org 的调用立即失效。
- 建议给 cron-job.org 账号开启 MFA。

## 八、如果 cron-job.org 不适合你

| 替代方案 | 说明 |
|---|---|
| **Cloudflare Workers Cron** | 免费、精度更好，PAT 可存加密 Secret（比明文请求头安全）；代价是要在控制台粘一段 JS |
| **腾讯云函数 SCF** | 国内访问最快、中文控制台，需实名认证；创建函数 + 定时触发器步骤较多 |
| **GitHub Pro（$4/月）** | 不是替代 cron，而是解锁「私有仓也能用 schedule」；但 GitHub 的 cron 本身依然不保证准时 |
| **本机 Windows 计划任务** | 完全自己可控，但依赖电脑开机 |

---

## 附：不想用网页配置时，可以先在本机验证 PAT

```bash
python <skill>/scripts/dispatch.py --repo liuxin19930310/workbuddy --workflow checkin.yml --pat <PAT>
```

它会触发一次并打印响应码与运行链接；加 `--print-cron-config` 还能直接输出本文第四节的
job 配置，便于复制粘贴。
