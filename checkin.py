#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WorkBuddy 「Buddy 加油站」每日签到（幂等）。

两种运行方式：
1) 云端（GitHub Actions / 任意 CI）：通过环境变量注入令牌
     WB_TOKEN   accessToken（必填，存在仓库 Secret 中）
     WB_DOMAIN  可选，默认 www.codebuddy.cn
2) 本机直接运行：未设置 WB_TOKEN 时，自动读取 WorkBuddy 桌面端登录态文件
     Windows: %LOCALAPPDATA%\\CodeBuddyExtension\\Data\\Public\\auth\\workbuddy-desktop.info
     macOS:   ~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info

可选：设置 SERVERCHAN_KEY 后，结果会推送到微信（Server 酱）。

退出码：0 = 签到成功或今日已签到；1 = 失败（令牌失效 / 网络异常 / 未知错误）
安全约定：全程不打印、不落盘任何令牌内容。
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

DEFAULT_DOMAIN = "www.codebuddy.cn"
STATUS_PATH = "/v2/billing/meter/checkin-activity-status"
CLAIM_PATH = "/v2/billing/meter/daily-checkin"
TIMEOUT = 30
ALREADY_CLAIMED_CODE = 10001

LOCAL_FILES = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""),
                 r"CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info"),
    os.path.expanduser(
        "~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info"),
]


def now_cn():
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S +08")


def read_local_credentials():
    """读取本机登录态文件，返回 (token, domain, path) 或 (None, None, None)。"""
    for path in LOCAL_FILES:
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    auth = (json.load(fh) or {}).get("auth") or {}
            except Exception:
                continue
            token = (auth.get("accessToken") or "").strip()
            if token:
                return token, (auth.get("domain") or DEFAULT_DOMAIN).strip(), path
    return None, None, None


def post(domain, path, token):
    """POST 空 JSON 体，返回 (http_status, parsed_body)。"""
    req = urllib.request.Request(
        "https://" + domain + path,
        data=b"{}",
        method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "WorkBuddy-Checkin-Action/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", "ignore")
        try:
            return err.code, json.loads(raw)
        except Exception:
            return err.code, {"code": -1, "msg": raw[:200]}
    except Exception as err:  # 网络层异常
        return 0, {"code": -1, "msg": "%s: %s" % (type(err).__name__, err)}


def notify(title, content):
    """可选：Server 酱微信推送。"""
    key = os.environ.get("SERVERCHAN_KEY", "").strip()
    if not key:
        return
    try:
        req = urllib.request.Request(
            "https://sctapi.ftqq.com/%s.send" % key,
            data=urllib.parse.urlencode({"title": title, "desp": content}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        urllib.request.urlopen(req, timeout=15).read()
    except Exception:
        pass


def emit(result, notify_it=True):
    line = json.dumps(result, ensure_ascii=False)
    print(line)
    if notify_it:
        notify("WorkBuddy 签到 %s" % result.get("status"), line)


def main():
    token = os.environ.get("WB_TOKEN", "").strip()
    domain = os.environ.get("WB_DOMAIN", "").strip()
    source = "env:WB_TOKEN"

    if not token:
        token, local_domain, path = read_local_credentials()
        if token:
            source = "local:%s" % path
            domain = domain or local_domain

    if not token:
        emit({"status": "error", "action": "none", "time": now_cn(),
              "msg": "未找到令牌：请设置环境变量 WB_TOKEN，或先在本机登录 WorkBuddy 桌面端"}, notify_it=False)
        return 1

    domain = domain or DEFAULT_DOMAIN

    status_code, status_body = post(domain, STATUS_PATH, token)
    if status_code in (401, 403) or status_body.get("code") in (401, 403):
        emit({"status": "error", "action": "none", "time": now_cn(), "source": source,
              "msg": "令牌已失效（HTTP %s），请重新获取 accessToken 并更新 Secret" % status_code})
        return 1
    if status_body.get("code") != 0:
        emit({"status": "error", "action": "none", "time": now_cn(), "source": source,
              "msg": "状态查询失败：%s" % status_body.get("msg")})
        return 1

    data = status_body.get("data") or {}
    if data.get("today_checked_in"):
        emit({"status": "ok", "action": "skip_already_signed", "time": now_cn(),
              "source": source, "streak_days": data.get("streak_days"),
              "today_credit": data.get("today_credit"), "msg": "今日已签到，无需重复领取"})
        return 0

    claim_code, claim_body = post(domain, CLAIM_PATH, token)
    body_code = claim_body.get("code")
    if claim_code in (401, 403) or body_code in (401, 403):
        emit({"status": "error", "action": "claim", "time": now_cn(), "source": source,
              "msg": "领取时令牌失效（HTTP %s）" % claim_code})
        return 1
    if body_code == ALREADY_CLAIMED_CODE:
        emit({"status": "ok", "action": "skip_already_signed", "time": now_cn(), "source": source,
              "msg": claim_body.get("msg") or "今日已签到"})
        return 0
    if body_code != 0:
        emit({"status": "error", "action": "claim", "time": now_cn(), "source": source,
              "msg": "领取失败：%s" % (claim_body.get("msg") or claim_code)})
        return 1

    claim_data = claim_body.get("data") or {}
    emit({"status": "ok", "action": "claimed", "time": now_cn(), "source": source,
          "credit": claim_data.get("today_credit") or claim_data.get("credit"),
          "streak_days": claim_data.get("streak_days"),
          "msg": "领取成功：+%s 积分，连续 %s 天" % (
              claim_data.get("today_credit") or claim_data.get("credit"),
              claim_data.get("streak_days"))})
    return 0


if __name__ == "__main__":
    sys.exit(main())
