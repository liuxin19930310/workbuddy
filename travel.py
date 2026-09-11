#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WorkBuddy「派猫猫旅行」自动闭环（幂等，纯标准库）。

玩法：把 Buddy 派去一个地点旅行 1~4 小时，回来后领取 5~10 积分；每日限一次。

本脚本每次运行只做「读一次状态 → 决定一个动作」，不做后台轮询：

    state=arrived            → 领取（claim，只领一次）
    state=traveling          → 跳过（绝不重复派出）
    daily_limit_reached=true → 跳过（今日额度已用尽）
    state=idle（且未达上限） → 派出（depart）

幂等由服务端状态保证：重复运行、定时器抖动都不会重复派或重复领。

令牌：复用签到那一个，无需浏览器 Cookie。
    WB_TOKEN   accessToken（必填，与 checkin.py 共用同一个 Secret）
    未设置时自动读取本机 WorkBuddy 登录态文件（便于本地调试）。

可选：SERVERCHAN_KEY 设置后，派出/领取/出错会推微信。

用法：
    python travel.py                # 巡检一轮
    python travel.py --dry-run      # 只查状态，不做任何写操作
    python travel.py --location 1   # 指定地点（1咖啡馆 2商场 3健身房 4客栈）

退出码：0 = 正常（含跳过）；1 = 失败
安全约定：全程不打印、不落盘任何令牌内容。
"""

import argparse
import json
import os
import random
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://www.workbuddy.cn/activity/growth/buddy/travel"
TIMEOUT = 30
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")
LOCATIONS = {1: "咖啡馆", 2: "商场店铺", 3: "健身房", 4: "古镇客栈"}

LOCAL_FILES = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""),
                 r"CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info"),
    os.path.expanduser(
        "~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info"),
]


# ---------------------------------------------------------------- 基础设施

def now_cn():
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S +08")


def read_local_token():
    for path in LOCAL_FILES:
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    auth = (json.load(fh) or {}).get("auth") or {}
            except Exception:
                continue
            tok = (auth.get("accessToken") or "").strip()
            if tok:
                return tok, "local:%s" % path
    return None, None


def call(token, path, method="GET", payload=None):
    """返回 (http_status, parsed_body)。"""
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9",
        "authorization": "Bearer " + token,
        "referer": "https://www.workbuddy.cn/profile/growth-center",
        "origin": "https://www.workbuddy.cn",
        "user-agent": UA,
        "x-client-platform": "web",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["content-type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"code": -1, "msg": raw[:200]}
    except Exception as e:
        return 0, {"code": -1, "msg": "%s: %s" % (type(e).__name__, e)}


def try_payloads(token, path, payloads):
    """写操作按顺序尝试多种字段名（接口未公开文档，抗字段改名）。"""
    last = None
    for payload in payloads:
        st, body = call(token, path, method="POST", payload=payload)
        if st in (401, 403) or body.get("code") in (401, 403):
            return False, body, payload, st
        if st == 200 and body.get("code") == 0:
            return True, body.get("data") or {}, payload, st
        last = (body, payload, st)
    if last:
        return False, last[0], last[1], last[2]
    return False, {"code": -1, "msg": "无可用 payload"}, {}, 0


# ---------------------------------------------------------------- 通知

def notify(title, content):
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


# 只在「派出成功 / 领取成功 / 出错」时推送；traveling 与已达上限的空跑静默
PUSH_ACTIONS = ("departed", "claimed")


def should_push(result):
    if result.get("status") == "error":
        return True
    return result.get("action") in PUSH_ACTIONS


def build_title(result):
    a = result.get("action")
    if result.get("status") == "error":
        return "WorkBuddy 猫猫旅行异常，需要处理"
    if a == "claimed":
        return "猫猫旅行归来 +%s 积分" % result.get("reward_credit", "?")
    if a == "departed":
        return "猫猫已出发 · %s" % result.get("location", "")
    return "WorkBuddy 猫猫旅行"


def build_body(result):
    lines = ["时间：%s" % result.get("time", ""),
             "状态：%s" % result.get("state", "-")]
    if result.get("action"):
        lines.append("动作：%s" % {
            "departed": "派出旅行", "claimed": "领取奖励",
            "skip_traveling": "旅行中，等待归来",
            "skip_daily_limit": "今日额度已用尽，跳过",
        }.get(result["action"], result["action"]))
    if result.get("location"):
        lines.append("地点：%s" % result["location"])
    if result.get("reward_credit"):
        lines.append("本次积分：+%s" % result["reward_credit"])
    if result.get("arrive_at"):
        lines.append("预计归来：%s" % result["arrive_at"])
    if result.get("status") == "error" and result.get("msg"):
        lines.append("原因：%s" % result["msg"])
    return "\n".join(lines)


def emit(result):
    print(json.dumps(result, ensure_ascii=False))
    if should_push(result):
        notify(build_title(result), build_body(result))


# ---------------------------------------------------------------- 主流程

def fmt_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), timezone(timedelta(hours=8))).strftime(
            "%Y-%m-%d %H:%M:%S +08")
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只查状态，不做写操作")
    ap.add_argument("--location", type=int, default=0,
                    help="地点 id（1咖啡馆 2商场 3健身房 4客栈），0=随机")
    args = ap.parse_args()

    token = os.environ.get("WB_TOKEN", "").strip()
    source = "env:WB_TOKEN"
    if not token:
        token, source = read_local_token()
    if not token:
        emit({"status": "error", "action": "none", "time": now_cn(),
              "msg": "未找到令牌：请设置环境变量 WB_TOKEN，或先在本机登录 WorkBuddy 桌面端"})
        return 1

    st, body = call(token, "/status")
    if st in (401, 403) or body.get("code") in (401, 403):
        emit({"status": "error", "action": "none", "time": now_cn(), "source": source,
              "msg": "令牌已失效（HTTP %s），请重新获取 accessToken 并更新 Secret" % st})
        return 1
    if body.get("code") != 0:
        emit({"status": "error", "action": "none", "time": now_cn(), "source": source,
              "msg": "状态查询失败：%s" % body.get("msg")})
        return 1

    d = body.get("data") or {}
    state = (d.get("state") or "").lower()
    limit = bool(d.get("daily_limit_reached"))
    record_id = d.get("record_id") or 0
    loc_name = (d.get("location") or {}).get("name")
    arrive_at = fmt_ts(d.get("arrive_at"))
    base = {"time": now_cn(), "state": state, "source": source,
            "daily_limit_reached": limit, "record_id": record_id,
            "location": loc_name, "arrive_at": arrive_at}

    if args.dry_run:
        emit(dict(base, status="ok", action="none", msg="dry-run：仅查询"))
        return 0

    # 1) 已归来 → 领取
    if state == "arrived":
        ok, data, used, st = try_payloads(token, "/claim", [
            {"record_id": record_id}, {"recordId": record_id}, {},
        ])
        if not ok:
            emit(dict(base, status="error", action="claim",
                      msg="领取失败：HTTP %s / %s" % (st, data.get("msg") or data.get("code"))))
            return 1
        emit(dict(base, status="ok", action="claimed",
                  reward_credit=data.get("reward_credit") or d.get("reward_credit") or 0,
                  msg="领取成功"))
        return 0

    # 2) 旅行中 → 跳过
    if state == "traveling":
        emit(dict(base, status="ok", action="skip_traveling", msg="旅行中，等待归来"))
        return 0

    # 3) 今日额度已用尽 → 跳过
    if limit:
        emit(dict(base, status="ok", action="skip_daily_limit", msg="今日额度已用尽"))
        return 0

    # 4) 空闲 → 派出
    loc_id = args.location if args.location in LOCATIONS else random.choice(list(LOCATIONS))
    code = {1: "coffee", 2: "mall", 3: "gym", 4: "inn"}[loc_id]
    ok, data, used, st = try_payloads(token, "/depart", [
        {"location_id": loc_id}, {"locationId": loc_id},
        {"location": loc_id}, {"location_code": code},
    ])
    if not ok:
        emit(dict(base, status="error", action="depart",
                  msg="派出失败：HTTP %s / %s" % (st, data.get("msg") or data.get("code"))))
        return 1
    emit(dict(base, status="ok", action="departed", location=LOCATIONS[loc_id],
              record_id=data.get("record_id") or record_id,
              arrive_at=fmt_ts(data.get("arrive_at")) or arrive_at,
              duration_hours=data.get("duration_hours"),
              msg="派出成功"))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
