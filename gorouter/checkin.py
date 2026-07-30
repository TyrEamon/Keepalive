#!/usr/bin/env python3
"""
GoRouter 每日签到脚本（GitHub Actions 专用）
使用 Bearer Token 认证 + Session Cookie 持久化。

需要的配置（环境变量）：
  GOROUTER_TOKEN     API Bearer Token
  GOROUTER_USER_ID   用户 ID
"""

import os
import sys
import json
import base64
import logging
from datetime import datetime, timezone, timedelta

import requests

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
BASE_URL = "https://gorouter.app"
TOKEN = os.getenv("GOROUTER_TOKEN") or ""
USER_ID = os.getenv("GOROUTER_USER_ID") or ""

# New-API 配额 → 美元换算：1 USD = 500000 quota
QUOTA_PER_UNIT = 500000

# 北京时间时区
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
    """用户名脱敏：显示前 4 位 + *****"""
    if not name:
        return "*****"
    return (name[:4] + "*****") if len(name) > 4 else (name + "*****")


def quota_to_usd(quota: int) -> float:
    """将 quota 转换为美元"""
    return round((quota or 0) / QUOTA_PER_UNIT, 2)


def bjt_date_str() -> str:
    """北京时间日期字符串，如 '2026年07月30日'"""
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
        log.warning("解析 GOROUTER_SESSION_COOKIE 失败: %s", e)
        return False


# ---------------------------------------------------------------------------
# 会话与 API
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
        "Content-Type": "application/json",
        "Authorization": f"Bearer {TOKEN}",
        "New-Api-User": USER_ID,
    })
    return session


def is_session_valid(session: requests.Session) -> bool:
    """检查当前 session 是否仍然有效"""
    url = f"{BASE_URL}/api/user/self"
    try:
        resp = session.get(url, timeout=15)
        return resp.json().get("success", False)
    except Exception:
        return False


def get_user_info(session: requests.Session) -> dict:
    """获取用户信息（含余额 quota）"""
    url = f"{BASE_URL}/api/user/self"
    try:
        resp = session.get(url, timeout=15)
        data = resp.json()
        if data.get("success"):
            return data.get("data", {})
        log.warning("获取用户信息失败: %s", data.get("message", ""))
        return {}
    except requests.RequestException as e:
        log.error("获取用户信息异常: %s", e)
        return {}


def get_checkin_status(session: requests.Session) -> dict:
    """查询签到状态，返回 data 字典"""
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
        log.info("🎉 签到成功！")
        return data.get("data", {})
    except requests.RequestException as e:
        log.error("签到请求异常: %s", e)
        return {}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    log.info("=" * 48)
    log.info("GoRouter 每日签到脚本启动")
    log.info("目标站点: %s", BASE_URL)
    log.info("用户 ID: %s", USER_ID)

    if not TOKEN:
        log.error("未配置 GOROUTER_TOKEN，脚本退出")
        sys.exit(1)

    if not USER_ID:
        log.error("未配置 GOROUTER_USER_ID，脚本退出")
        sys.exit(1)

    # 1. 创建 session
    session = create_session()

    # 2. 尝试从 GOROUTER_SESSION_COOKIE 变量恢复 session
    session_encoded = os.getenv("GOROUTER_SESSION_COOKIE") or ""
    session_valid = False
    if session_encoded:
        log.info("检测到 GOROUTER_SESSION_COOKIE 变量，尝试恢复 session...")
        if b64_to_session(session, session_encoded) and is_session_valid(session):
            log.info("✅ session 有效，跳过认证")
            session_valid = True
        else:
            log.info("session 已失效，需要重新认证")
            # 重新设置 Bearer Token（b64_to_session 可能覆盖了 headers）
            session.headers["Authorization"] = f"Bearer {TOKEN}"

    if not session_valid:
        log.info("🔑 使用 Bearer Token 认证")
        # Token 已经在 headers 中，验证一下
        info = get_user_info(session)
        if not info:
            log.error("Token 认证失败，请检查 GOROUTER_TOKEN 是否有效，脚本退出")
            sys.exit(1)
        log.info("✅ 认证成功: %s", mask(info.get("display_name", "")))

    # 3. 获取用户信息（含余额）
    user_info = get_user_info(session)
    if not user_info:
        log.error("无法获取用户信息，脚本退出")
        sys.exit(1)

    username = mask(user_info.get("display_name", "") or user_info.get("username", ""))
    current_quota = user_info.get("quota", 0)
    balance_usd = quota_to_usd(current_quota)
    log.info("当前余额: $%.2f", balance_usd)

    # 4. 查询今日签到状态
    log.info("查询签到状态...")
    checkin_data = get_checkin_status(session)
    if not checkin_data:
        log.error("无法获取签到状态，脚本退出")
        sys.exit(1)

    stats = checkin_data.get("stats", {})
    checked_in_today = stats.get("checked_in_today", False)

    # 5. 构建通知数据
    notify_data = {
        "username": username,
        "date": bjt_date_str(),
        "checked_in": checked_in_today,
        "reward_usd": 0.0,
        "balance_usd": balance_usd,
    }

    if checked_in_today:
        log.info("✅ 今日已签到")

        # 从 records 提取奖励金额（仅首次签到有记录）
        records = stats.get("records", [])
        if records:
            last = records[-1]
            if last.get("quota_awarded"):
                reward = quota_to_usd(last["quota_awarded"])
                log.info("上次签到奖励: $%.2f", reward)
    else:
        log.info("⏳ 今日未签到，执行签到...")
        result = do_checkin(session)
        if result.get("already_checked_in"):
            notify_data["checked_in"] = True
        else:
            award_quota = result.get("quota_awarded", 0)
            reward_usd = quota_to_usd(award_quota)
            notify_data["reward_usd"] = reward_usd
            log.info("获得奖励 quota: %s (≈ $%.2f)", award_quota, reward_usd)

            # 签到后刷新余额
            user_info = get_user_info(session)
            if user_info:
                new_quota = user_info.get("quota", 0)
                notify_data["balance_usd"] = quota_to_usd(new_quota)
                log.info("签到后余额: $%.2f", notify_data["balance_usd"])

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
