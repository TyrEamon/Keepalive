#!/usr/bin/env python3
"""
AgentRouter 每日签到脚本（GitHub Actions 专用）
使用「系统访问令牌」免登录调用接口：查询签到状态、执行签到、获取余额，
并通过 TG 发送通知。

说明：agentrouter.org 使用 GitHub OAuth 登录，无法用账号密码自动登录，
因此改用站点「个人设置 → 系统访问令牌」生成的 Access Token 调用管理 API。
注意：New-API 管理接口的 Authorization 头需直接放 token，【不能】加 "Bearer " 前缀。
"""

import os
import sys
import logging
from datetime import datetime, timezone, timedelta
import requests

# ---------------------------------------------------------------------------
# 配置（从环境变量读取）
# ---------------------------------------------------------------------------
BASE_URL = os.getenv("AGENTROUTER_BASE_URL") or "https://agentrouter.org"
# 系统访问令牌（个人设置中生成，长期有效）
ACCESS_TOKEN = os.getenv("AGENTROUTER_ACCESS_TOKEN") or ""
# 用户数字 ID（管理 API 需要的 New-API-User 头）
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

def quota_to_usd(quota: int) -> float:
    """将 quota 转换为美元"""
    return quota / QUOTA_PER_UNIT


def bjt_date_str() -> str:
    """北京时间日期字符串，如 '2026年07月09日'"""
    now = datetime.now(BJT)
    return f"{now.year}年{now.month:02d}月{now.day:02d}日"


# ---------------------------------------------------------------------------
# API 调用
# ---------------------------------------------------------------------------

def create_session() -> requests.Session:
    """创建预配置的 requests Session（携带访问令牌与用户 ID 头）"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Authorization": ACCESS_TOKEN,
        "New-API-User": USER_ID,
    })
    return session


def is_token_valid(session: requests.Session) -> bool:
    """检查访问令牌是否有效（能否获取用户信息）"""
    url = f"{BASE_URL}/api/user/self"
    try:
        resp = session.get(url, timeout=15)
        return resp.json().get("success", False)
    except Exception as e:
        log.error("校验访问令牌异常: %s", e)
        return False


def get_username(session: requests.Session) -> str:
    """获取当前登录用户名（用于通知展示）"""
    url = f"{BASE_URL}/api/user/self"
    try:
        resp = session.get(url, timeout=15)
        data = resp.json()
        if data.get("success"):
            return data.get("data", {}).get("username", "") or "user"
    except Exception:
        pass
    return "user"


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
        log.info("签到成功！")
        return data.get("data", {})
    except requests.RequestException as e:
        log.error("签到请求异常: %s", e)
        return {}


def get_user_quota(session: requests.Session) -> int:
    """获取用户剩余 quota"""
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

    # 0. 校验必填配置
    if not ACCESS_TOKEN:
        log.error("未配置 AGENTROUTER_ACCESS_TOKEN，脚本退出")
        sys.exit(1)
    if not USER_ID:
        log.error("未配置 AGENTROUTER_USER_ID，脚本退出")
        sys.exit(1)

    # 1. 创建带令牌的 session
    session = create_session()

    # 2. 校验令牌有效性
    log.info("校验访问令牌...")
    if not is_token_valid(session):
        log.error("访问令牌无效或已失效，请在站点重新生成 AGENTROUTER_ACCESS_TOKEN")
        sys.exit(1)
    log.info("访问令牌有效")

    username = get_username(session)

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
        "username": username,
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

    # 6. 发送 TG 通知
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
