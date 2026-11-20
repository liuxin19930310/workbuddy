"""GitHub Actions 入口：领取 Qoder CN 每日 Credits 并按结果推送 Bark。

规则：
  - 只在「真领到」或「失败」时推送；已领过 / 无活动静默，避免一天 5 次刷屏。
  - token 过期（AUTH_REJECTED / NO_AUTH）单独告警，提示更新 Secret。
  - 退出码跟随 qoder_signin.py：0 正常，非 0 失败。
"""
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BARK_SERVER = "https://api.day.app"


def bark_target():
    """兼容纯 key / App 首页整条测试 URL / 自建域名，照抄仓库 checkin.py 的实现。"""
    raw = (os.environ.get("BARK_KEY") or "").strip()
    if not raw:
        return None, None, "未配置 BARK_KEY"
    server = (os.environ.get("BARK_SERVER") or "").strip() or DEFAULT_BARK_SERVER
    key = raw
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urllib.parse.urlparse(raw)
        segs = [s for s in parsed.path.split("/") if s]
        if not segs:
            return None, None, "BARK_KEY 是 URL 但解析不出 key"
        server, key = "%s://%s" % (parsed.scheme, parsed.netloc), segs[0]
    if len(key) < 12 or not re.fullmatch(r"[A-Za-z0-9_\-]+", key):
        return None, None, "BARK_KEY 形态不对（长度 %d）" % len(key)
    return server, key, None


def bark(title, body, group="qoder", level="active"):
    server, key, problem = bark_target()
    if problem:
        print("Bark 跳过:", problem)
        return
    payload = {"title": title, "body": body, "group": group, "level": level}
    req = urllib.request.Request(
        "%s/%s" % (server.rstrip("/"), key),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "qoder-claim"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read().decode("utf-8", "ignore"))
            print("Bark 推送:", resp)
    except Exception as e:
        print("Bark 推送失败:", type(e).__name__, str(e)[:150])


def arg_value(flag, default=""):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main():
    force = "--force-notify" in sys.argv
    label = arg_value("--label", "Qoder").strip() or "Qoder"
    secret = arg_value("--secret", "QODER_TOKEN").strip() or "QODER_TOKEN"
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
    print("account=%s result=%s report=%s" % (label, result, report))

    if result == "CLAIMED":
        bark("%s 领取成功" % label, report, level="active")
    elif result in ("AUTH_REJECTED", "NO_AUTH", "AUTH_ERROR"):
        bark("%s 令牌失效" % label,
             report + "（需更新仓库 Secret %s）" % secret, level="timeSensitive")
    elif result in ("NETWORK", "ERROR", "FORBIDDEN"):
        bark("%s 领取异常" % label, report, level="timeSensitive")
    elif force:
        bark("%s 手动验证" % label, "result=%s %s" % (result, report), level="passive")

    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
