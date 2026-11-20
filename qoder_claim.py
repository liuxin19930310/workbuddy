"""GitHub Actions 入口：领取 Qoder CN 每日 Credits 并按结果推送 Bark。

规则：
  - 只在「真领到」或「失败」时推送；已领过 / 无活动静默，避免一天 5 次刷屏。
  - token 过期（AUTH_REJECTED / NO_AUTH）单独告警，提示更新 Secret。
  - 退出码跟随 qoder_signin.py：0 正常，非 0 失败。
"""
import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def bark(title, body, group="qoder", level="active"):
    key = (os.environ.get("BARK_KEY") or "").strip()
    if not key:
        print("BARK_KEY 未配置，跳过推送")
        return
    payload = {
        "title": title,
        "body": body,
        "group": group,
        "level": level,
    }
    server = (os.environ.get("BARK_SERVER") or "https://api.day.app").rstrip("/")
    req = urllib.request.Request(
        server + "/" + key,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "qoder-claim"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print("Bark 推送 HTTP", r.status)
    except Exception as e:
        print("Bark 推送失败:", type(e).__name__, str(e)[:150])


def main():
    force = "--force-notify" in sys.argv
    proc = subprocess.run(
        [sys.executable, os.path.join(HERE, "qoder_signin.py"), "auto"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = {}
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                out = json.loads(line)
            except json.JSONDecodeError:
                pass
    result = out.get("result", "UNKNOWN")
    report = out.get("report") or (proc.stderr or "").strip()[:200] or "无详情"
    print("result=%s report=%s" % (result, report))

    if result == "CLAIMED":
        bark("Qoder 领取成功", report, level="active")
    elif result in ("AUTH_REJECTED", "NO_AUTH", "AUTH_ERROR"):
        bark("Qoder 令牌失效", report + "（需更新仓库 Secret QODER_TOKEN）", level="timeSensitive")
    elif result in ("NETWORK", "ERROR", "FORBIDDEN"):
        bark("Qoder 领取异常", report, level="timeSensitive")
    elif force:
        bark("Qoder 手动验证", "result=%s %s" % (result, report), level="passive")

    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
