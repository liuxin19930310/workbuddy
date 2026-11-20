#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TRAE CN 每日签到（GitHub Actions 版）。

凭证来自仓库 Secret：
  TRAE_TOKEN      = 本机 Trae CN 桌面端登录态解出的 JWT（约 14 天有效）
  TRAE_DEVICE_ID  = 16 位数字设备号（UUID 会触发 9074 风控）

流程：先查 status → 未签才 claim（幂等）→ 9074 时换设备号重试 → Bark 推送。
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.trae.cn"
STATUS = "/trae/api/v2/ug/checkin_credits/status"
CLAIM = "/trae/api/v2/ug/checkin_credits/claim"
UA = "TraeCheckin/1.0"


def post(path, headers, timeout=25):
    req = urllib.request.Request(
        BASE + path, data=b"{}", headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, str(e)


def parse(text):
    try:
        return json.loads(text)
    except Exception:
        return {}


def headers(token, device_id):
    return {
        "Authorization": "Cloud-IDE-JWT " + token,
        "X-User-Region": "CN",
        "Content-Type": "application/json",
        "User-Agent": UA,
        "x-device-id": device_id,
    }


def bark_target():
    """兼容 BARK_KEY 存整条测试 URL 或纯 key 两种形态（照抄仓库 checkin.py）。"""
    raw = (os.environ.get("BARK_KEY") or "").strip()
    if not raw:
        return None
    m = re.match(r"^https?://[^/]+/(?:push/)?([^/?#]+)", raw)
    key = m.group(1) if m else raw
    m2 = re.search(r"[?&]key=([^&#\s]+)", raw)
    if m2:
        key = urllib.parse.unquote(m2.group(1))
    server = (os.environ.get("BARK_SERVER") or "https://api.day.app").rstrip("/")
    return server + "/" + key


def bark(title, body, level="active"):
    target = bark_target()
    if not target:
        print("BARK_KEY 未配置，跳过推送")
        return
    payload = json.dumps({
        "title": title, "body": body, "group": "trae", "level": level
    }).encode("utf-8")
    req = urllib.request.Request(
        target, data=payload,
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "trae-claim"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print("Bark 推送 HTTP", r.status)
    except Exception as e:
        print("Bark 推送失败:", type(e).__name__, str(e)[:150])


def main():
    token = (os.environ.get("TRAE_TOKEN") or "").strip()
    device_id = (os.environ.get("TRAE_DEVICE_ID") or "").strip()
    force = "--force-notify" in sys.argv
    if not token:
        print("缺少 TRAE_TOKEN")
        return 2
    if not re.fullmatch(r"\d{16}", device_id):
        print("TRAE_DEVICE_ID 非法（必须 16 位数字）:", device_id[:20])
        return 2

    h = headers(token, device_id)
    scode, stext = post(STATUS, h)
    if scode in (401, 403):
        print("result=AUTH_REJECTED report=登录态失效 HTTP %s" % scode)
        bark("TRAE 令牌失效", "需更新仓库 Secret TRAE_TOKEN", "timeSensitive")
        return 1
    if scode != 200:
        print("result=ERROR report=状态接口 HTTP %s" % scode)
        bark("TRAE 签到异常", "status HTTP %s" % scode, "timeSensitive")
        return 1

    s = parse(stext)
    if not s.get("enable"):
        print("result=INACTIVE report=签到活动未开启")
        if force:
            bark("TRAE 手动验证", "活动未开启", "passive")
        return 0
    credits = s.get("credits")
    if s.get("checked_in"):
        msg = "今日已签过（积分 %s）" % credits if credits is not None else "今日已签过"
        print("result=ALREADY report=%s" % msg)
        if force:
            bark("TRAE 手动验证", msg, "passive")
        return 0

    # 未签 → 领取。9074 风控换设备号重试。
    ccode, ctext = post(CLAIM, h)
    c = parse(ctext)
    code = c.get("code", -1)
    attempt = 1
    while code == 9074 and attempt < 5:
        import random
        new_dev = str(random.randint(10 ** 15, 10 ** 16 - 1))
        print("命中 9074，换设备号重试(%d): %s" % (attempt, new_dev))
        h = headers(token, new_dev)
        ccode, ctext = post(CLAIM, h)
        c = parse(ctext)
        code = c.get("code", -1)
        attempt += 1

    if ccode == 200 and (code == 0 or c.get("checked_in")):
        got = (c.get("data") or {}).get("credits") if isinstance(c.get("data"), dict) else None
        msg = ("成功领取 %s 积分" % got) if got else "签到成功"
        print("result=CLAIMED report=%s" % msg)
        bark("TRAE 领取成功", msg, "active")
        return 0

    if code in (9095, 9074) or "已签" in str(c.get("message", "")):
        msg = "今日已签（%s）" % c.get("message", "")
        print("result=ALREADY report=%s" % msg)
        if force:
            bark("TRAE 手动验证", msg, "passive")
        return 0

    if ccode in (401, 403):
        print("result=AUTH_REJECTED report=领取 HTTP %s" % ccode)
        bark("TRAE 令牌失效", "需更新仓库 Secret TRAE_TOKEN", "timeSensitive")
        return 1

    reason = c.get("message") or ctext[:200] if isinstance(c, dict) else ctext[:200]
    print("result=ERROR report=领取失败 HTTP %s %s" % (ccode, reason))
    bark("TRAE 领取异常", str(reason)[:200], "timeSensitive")
    return 1


if __name__ == "__main__":
    sys.exit(main())
