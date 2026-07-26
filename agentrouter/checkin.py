#!/usr/bin/env python3
"""
AgentRouter 每日签到脚本（GitHub Actions 专用）

需要的配置（环境变量）：
  AGENTROUTER_USERNAME  登录用户名或邮箱
  AGENTROUTER_PASSWORD  登录密码
  SOCKS5_PROXY          代理，可多个（换行或逗号分隔），如
                        socks5://user:pass@host:port
"""

import os
import re
import sys
import logging
from datetime import datetime, timezone, timedelta
import requests

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
BASE_URL = os.getenv("AGENTROUTER_BASE_URL") or "https://agentrouter.org"
USERNAME = os.getenv("AGENTROUTER_USERNAME") or ""
PASSWORD = os.getenv("AGENTROUTER_PASSWORD") or ""
PROXY_ENV = os.getenv("SOCKS5_PROXY") or ""

# New-API 配额 → 美元换算：1 USD = 500000 quota
QUOTA_PER_UNIT = 500000
BJT = timezone(timedelta(hours=8))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("checkin")


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def mask(name: str) -> str:
    """用户名/邮箱脱敏：显示前 4 位 + ●●●●●"""
    if not name:
        return "●●●●●"
    return (name[:4] + "●●●●●") if len(name) > 4 else (name + "●●●●●")


def quota_to_usd(quota: int) -> float:
    return (quota or 0) / QUOTA_PER_UNIT


def bjt_date_str() -> str:
    now = datetime.now(BJT)
    return f"{now.year}年{now.month:02d}月{now.day:02d}日"


def parse_proxies(raw: str) -> list:
    """解析代理配置（换行或逗号分隔），socks/socks5 统一转 socks5h（DNS 也走代理）"""
    proxies = []
    for item in re.split(r"[\n,]+", raw):
        url = item.strip()
        if not url:
            continue
        if url.startswith("socks5://"):
            url = "socks5h://" + url[len("socks5://"):]
        elif url.startswith("socks://"):
            url = "socks5h://" + url[len("socks://"):]
        proxies.append(url)
    return proxies


def get_json(resp: requests.Response):
    """解析 JSON；被 WAF 拦截或非 JSON 时返回 None"""
    try:
        return resp.json()
    except ValueError:
        if "aliyun_waf" in resp.text:
            log.warning("响应被阿里云 WAF 拦截（当前 IP/代理不可用）")
        return None


# ---------------------------------------------------------------------------
# 会话与 API
# ---------------------------------------------------------------------------

def create_session(proxy_url: str = "") -> requests.Session:
    """创建仿浏览器 Session（可选代理）"""
    s = requests.Session()
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/125.0.0.0 Safari/537.36"),
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Referer": f"{BASE_URL}/login",
        "Origin": BASE_URL,
    })
    if proxy_url:
        s.proxies.update({"http": proxy_url, "https": proxy_url})
    return s


def build_working_session():
    """逐个尝试代理 + 直连，返回第一个能绕过 WAF 的 session；全部失败返回 None"""
    proxies = parse_proxies(PROXY_ENV)
    attempts = [(p, p.split("@")[-1]) for p in proxies] + [("", "直连（无代理）")]
    for proxy_url, label in attempts:
        log.info("尝试连接方式: %s", label)
        s = create_session(proxy_url)
        try:
            data = get_json(s.get(f"{BASE_URL}/api/status", timeout=25))
            if data and data.get("success"):
                log.info("✅ 连接可用：%s（已绕过 WAF）", label)
                return s
        except Exception as e:
            log.warning("连接 [%s] 异常: %s", label, e)
        log.warning("❌ 连接 [%s] 不可用，尝试下一个", label)
    return None


def do_login(session: requests.Session) -> dict:
    """登录（= 触发签到），返回用户 data（含 checked_in / access_token / id）"""
    payload = {"username": USERNAME, "password": PASSWORD}
    try:
        data = get_json(session.post(f"{BASE_URL}/api/user/login", json=payload, timeout=25))
        if not data:
            return {}
        if data.get("success"):
            log.info("登录成功: %s", mask(USERNAME))
            return data.get("data", {})
        log.error("登录失败: %s", data.get("message", "未知错误"))
        return {}
    except requests.RequestException as e:
        log.error("登录请求异常: %s", e)
        return {}


def get_quota(session: requests.Session, access_token: str, user_id) -> int:
    """用登录得到的 access_token 查询最新余额 quota"""
    try:
        headers = {"Authorization": access_token, "New-API-User": str(user_id)}
        data = get_json(session.get(f"{BASE_URL}/api/user/self", headers=headers, timeout=25))
        if data and data.get("success"):
            return data.get("data", {}).get("quota", 0)
    except requests.RequestException as e:
        log.error("查询余额异常: %s", e)
    return 0


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    log.info("=" * 48)
    log.info("AgentRouter 每日签到脚本启动")
    log.info("签到用户: %s", mask(USERNAME))

    if not USERNAME or not PASSWORD:
        log.error("未配置 AGENTROUTER_USERNAME / AGENTROUTER_PASSWORD，脚本退出")
        sys.exit(1)

    # 1. 选出能绕过 WAF 的连接
    session = build_working_session()
    if session is None:
        log.error("所有连接方式均无法绕过 WAF，请检查 SOCKS5_PROXY，脚本退出")
        sys.exit(1)

    # 2. 登录触发签到
    log.info("登录中（登录即触发每日签到）...")
    user = do_login(session)
    if not user:
        log.error("登录失败，无法签到，脚本退出")
        sys.exit(1)

    new_checkin = bool(user.get("checked_in"))  # True=本次触发了新签到
    if new_checkin:
        log.info("🎉 签到成功，新增额度已到账")
    else:
        log.info("✅ 今日已签到")

    # 3. 查询最新余额（登录响应中的 quota 可能尚未刷新）
    quota = get_quota(session, user.get("access_token", ""), user.get("id", ""))
    balance_usd = round(quota_to_usd(quota), 2)
    log.info("当前余额: $%.2f", balance_usd)

    # 4. 发送 TG 通知
    notify_data = {
        "username": mask(user.get("username") or USERNAME),
        "date": bjt_date_str(),
        "checked_in": not new_checkin,  # notify: True=今日已签到, False=新签到
        "reward_usd": 0.0,
        "balance_usd": balance_usd,
    }
    try:
        from notify import send_tg_notification  # type: ignore
        send_tg_notification(notify_data)
    except ImportError as e:
        log.warning("无法导入 notify 模块: %s", e)
    except Exception as e:
        log.error("发送 TG 通知异常: %s", e)

    log.info("签到流程完成")
    log.info("=" * 48)


if __name__ == "__main__":
    main()
