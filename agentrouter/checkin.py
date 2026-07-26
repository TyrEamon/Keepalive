#!/usr/bin/env python3
"""
AgentRouter 每日签到脚本（GitHub Actions 专用）
自动登录、查询签到状态、执行签到、获取余额，通过 TG 发送通知
"""

import os
import sys
import json
import base64
import logging
from datetime import datetime, timezone, timedelta
import requests

# ---------------------------------------------------------------------------
# 配置（从环境变量读取）
# ---------------------------------------------------------------------------
BASE_URL = os.getenv("AGENTROUTER_BASE_URL") or "https://agentrouter.org"
USERNAME = os.getenv("AGENTROUTER_USERNAME") or ""
PASSWORD = os.getenv("AGENTROUTER_PASSWORD") or ""
USER_ID = os.getenv("AGENTROUTER_USER_ID") or ""

# New-API 配额 → 美元换算：1 USD = 500000 quota（如与实际不符可调整此值）
QUOTA_PER_UNIT = 500000

# 北京时间时区
BJT = timezone(timedelta(hours=8))

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("checkin")


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def mask_username(name: str) -> str:
    """用户名脱敏：显示前 4 位 + ●●●●●"""
    if not name:
        return "●●●●●"
    return (name[:4] + "●●●●●") if len(name) > 4 else (name + "●●●●●")


def quota_to_usd(quota: int) -> float:
    """将 quota 转换为美元"""
    return quota / QUOTA_PER_UNIT


def bjt_date_str() -> str:
    """北京时间日期字符串，如 '2026年07月09日'"""
    now = datetime.now(BJT)
    return f"{now.year}年{now.month:02d}月{now.day:02d}日"


# ---------------------------------------------------------------------------
# Session 序列化（JSON → base64，适配 GitHub Actions Variables）
# ---------------------------------------------------------------------------

def session_to_b64(session: requests.Session) -> str:
    """将 session cookies 序列化为 base64 字符串"""
    cookies_list = []
    for cookie in session.cookies:
        cookies_list.append({
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path,
            "secure": cookie.secure,
            "expires": cookie.expires,
        })
    return base64.b64encode(json.dumps(cookies_list).encode()).decode()


def b64_to_session(session: requests.Session, encoded: str) -> bool:
    """从 base64 字符串恢复 session cookies"""
    try:
        cookies_list = json.loads(base64.b64decode(encoded))
        for c in cookies_list:
            session.cookies.set(
                c["name"], c["value"],
                domain=c.get("domain"),
                path=c.get("path", "/"),
                secure=c.get("secure", False),
            )
        return True
    except Exception as e:
        log.warning("解析 SESSION_COOKIE 失败: %s", e)
        return False


# ---------------------------------------------------------------------------
# API 调用
# ---------------------------------------------------------------------------

def create_session() -> requests.Session:
    """创建预配置的 requests Session"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
    })
    return session


def _set_auth_header(session: requests.Session):
    """为管理 API 请求添加 New-Api-User 头"""
    session.headers["New-Api-User"] = USER_ID


def api_login(session: requests.Session) -> bool:
    """登录并获取 session cookie"""
    url = f"{BASE_URL}/api/user/login"
    payload = {"username": USERNAME, "password": PASSWORD}
    try:
        resp = session.post(url, json=payload, timeout=30)
        data = resp.json()
        if data.get("success"):
            log.info("登录成功: %s", mask_username(USERNAME))
            return True
        log.error("登录失败: %s", data.get("message", "未知错误"))
        return False
    except requests.RequestException as e:
        log.error("登录请求异常: %s", e)
        return False


def is_session_valid(session: requests.Session) -> bool:
    """检查当前 session 是否仍然有效"""
    _set_auth_header(session)
    url = f"{BASE_URL}/api/user/self"
    try:
        resp = session.get(url, timeout=15)
        return resp.json().get("success", False)
    except Exception:
        return False


def get_checkin_status(session: requests.Session) -> dict:
    """查询签到状态，返回 data 字典"""
    _set_auth_header(session)
    url = f"{BASE_URL}/api/user/checkin"
    try:
        resp = session.get(url, timeout=15)
        data = resp.json()
        if data.get("success"):
            return data.get("data", {})
        log.warning("查询签到状态失败: %s", data.get("message", ""))
        return {}
    except requests.RequestException as e:
        log.error("查询签到状态异常: %s", e)
        return {}


def do_checkin(session: requests.Session) -> dict:
    """执行签到，返回签到结果 data 字典"""
    _set_auth_header(session)
    url = f"{BASE_URL}/api/user/checkin"
    try:
        resp = session.post(url, timeout=15)
        data = resp.json()

        msg = data.get("message", "")
        if not data.get("success"):
            if "今日已签到" in msg:
                log.info("执行签到返回: 今日已签到（可能并发重复）")
                return {"already_checked_in": True}
            log.warning("签到失败: %s", msg)
            return {}
        log.info("签到成功！")
        return data.get("data", {})
    except requests.RequestException as e:
        log.error("签到请求异常: %s", e)
        return {}


def get_user_quota(session: requests.Session) -> int:
    """获取用户剩余 quota"""
    _set_auth_header(session)
    url = f"{BASE_URL}/api/user/self"
    try:
        resp = session.get(url, timeout=15)
        data = resp.json()
        if data.get("success"):
            quota = data.get("data", {}).get("quota", 0)
            log.info("当前 quota: %s (≈ $%.2f)", quota, quota_to_usd(quota))
            return quota
        log.warning("获取余额失败: %s", data.get("message", ""))
        return 0
    except requests.RequestException as e:
        log.error("获取余额异常: %s", e)
        return 0


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    log.info("=" * 48)
    log.info("AgentRouter 每日签到脚本启动")
    log.info("目标站点: %s", BASE_URL)
    log.info("签到用户: %s", mask_username(USERNAME))

    # 1. 创建 session
    session = create_session()

    # 2. 尝试从 SESSION_COOKIE 变量恢复 session（仅 GitHub Actions 环境）
    session_encoded = os.getenv("AGENTROUTER_SESSION_COOKIE") or ""
    session_valid = False
    if session_encoded:
        log.info("检测到 AGENTROUTER_SESSION_COOKIE 变量，尝试恢复 session...")
        if b64_to_session(session, session_encoded) and is_session_valid(session):
            log.info("session 有效，跳过登录")
            session_valid = True
        else:
            log.info("session 已失效，需要重新登录")

    if not session_valid:
        log.info("登录中...")
        if not api_login(session):
            log.error("登录失败，脚本退出")
            sys.exit(1)

    # 3. 查询今日签到状态
    log.info("查询签到状态...")
    checkin_data = get_checkin_status(session)
    if not checkin_data:
        log.error("无法获取签到状态，脚本退出")
        sys.exit(1)

    stats = checkin_data.get("stats", {})
    checked_in_today = stats.get("checked_in_today", False)

    # 4. 构建通知数据
    notify_data = {
        "username": mask_username(USERNAME),
        "date": bjt_date_str(),
        "checked_in": checked_in_today,
        "reward_usd": 0.0,
        "balance_usd": 0.0,
    }

    if checked_in_today:
        log.info("✅ 今日已签到")
    else:
        log.info("⏳ 今日未签到，执行签到...")
        result = do_checkin(session)
        if result.get("already_checked_in"):
            notify_data["checked_in"] = True
        else:
            reward_quota = result.get("quota_awarded", 0)
            notify_data["reward_usd"] = round(quota_to_usd(reward_quota), 2)
            log.info("获得奖励 quota: %s (≈ $%.2f)", reward_quota, notify_data["reward_usd"])

    # 5. 获取最新余额
    quota = get_user_quota(session)
    notify_data["balance_usd"] = round(quota_to_usd(quota), 2)

    # 6. 将 session 写回 Variables（生成 session.cookie.b64 供 workflow 的 gh variable set 使用）
    session_b64 = session_to_b64(session)
    with open("session.cookie.b64", "w") as f:
        f.write(session_b64)
    log.info("session 已编码写入 session.cookie.b64（供 gh variable set 写回 Variables）")

    # 7. 发送 TG 通知
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
