#!/usr/bin/env python3
"""
GoRouter 多账号每日签到（GitHub Actions）

从环境变量 GOROUTER_ACCOUNTS_JSON 读取账号列表。

每个账号默认使用：
- user_id
- session Cookie

也可以为个别账号选填 Bearer token。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests


BASE_URL = "https://gorouter.app"

# GoRouter / New-API 配额换算
# 500000 quota = 1 USD
QUOTA_PER_USD = 500_000

# 北京时间
BJT = timezone(timedelta(hours=8))

REQUEST_TIMEOUT = 20


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

log = logging.getLogger("gorouter-checkin")


def quota_to_usd(quota: Any) -> float:
    """将 quota 换算为美元。"""
    try:
        return round(float(quota or 0) / QUOTA_PER_USD, 2)
    except (TypeError, ValueError):
        return 0.0


def today_text() -> str:
    """返回北京时间日期。"""
    now = datetime.now(BJT)
    return f"{now.year}年{now.month:02d}月{now.day:02d}日"


def mask_name(value: str) -> str:
    """对站内用户名进行简单脱敏。"""
    value = str(value or "").strip()

    if not value:
        return ""

    if len(value) <= 4:
        return value[0] + "***"

    return value[:4] + "*****"


def normalize_session_value(value: Any) -> str:
    """
    标准化 Session。

    支持两种填写方式：
    1. 只填写 Cookie 值
    2. 填写 session=xxxxx
    """
    session_value = str(value or "").strip()

    if session_value.lower().startswith("session="):
        session_value = session_value.split("=", 1)[1].strip()

    return session_value


def load_accounts() -> list[dict[str, Any]]:
    """读取并验证多账号配置。"""
    raw = os.getenv("GOROUTER_ACCOUNTS_JSON", "").strip()

    if not raw:
        raise ValueError("未配置 GOROUTER_ACCOUNTS_JSON")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "GOROUTER_ACCOUNTS_JSON 不是有效 JSON："
            f"第 {exc.lineno} 行第 {exc.colno} 列"
        ) from exc

    if not isinstance(data, list):
        raise ValueError("GOROUTER_ACCOUNTS_JSON 顶层必须是数组 []")

    accounts: list[dict[str, Any]] = []

    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            log.warning("跳过第 %d 项：账号配置必须是对象", index)
            continue

        # enabled=false 时跳过账号
        if item.get("enabled", True) is False:
            log.info(
                "跳过已禁用账号：%s",
                item.get("name") or f"账号{index}",
            )
            continue

        account = dict(item)

        account["name"] = str(
            item.get("name") or f"账号{index}"
        ).strip()

        account["user_id"] = str(
            item.get("user_id") or ""
        ).strip()

        account["session"] = normalize_session_value(
            item.get("session")
        )

        # token 为可选项
        account["token"] = str(
            item.get("token") or ""
        ).strip()

        if not account["user_id"]:
            log.warning(
                "跳过 %s：缺少 user_id",
                account["name"],
            )
            continue

        if not account["session"] and not account["token"]:
            log.warning(
                "跳过 %s：session 和 token 至少填写一个",
                account["name"],
            )
            continue

        accounts.append(account)

    if not accounts:
        raise ValueError(
            "没有可运行的账号，请检查 enabled、user_id 和 session"
        )

    return accounts


def create_session(
    account: dict[str, Any],
) -> requests.Session:
    """创建带有账号 Cookie 和请求头的 Session。"""
    session = requests.Session()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/150.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Referer": f"{BASE_URL}/dashboard/overview",
        "New-Api-User": account["user_id"],
    }

    # 个别账号若有 Bearer Token，也可以填写
    if account.get("token"):
        headers["Authorization"] = (
            f"Bearer {account['token']}"
        )

    session.headers.update(headers)

    if account.get("session"):
        session.cookies.set(
            "session",
            account["session"],
            domain="gorouter.app",
            path="/",
            secure=True,
        )

    return session


def request_json(
    session: requests.Session,
    method: str,
    path: str,
) -> tuple[bool, dict[str, Any], str]:
    """
    请求 GoRouter API。

    返回：
    ok, data, message
    """
    url = f"{BASE_URL}{path}"

    try:
        response = session.request(
            method,
            url,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        return False, {}, f"请求异常：{exc}"

    try:
        payload = response.json()
    except ValueError:
        return (
            False,
            {},
            f"HTTP {response.status_code}，返回内容不是 JSON",
        )

    if not isinstance(payload, dict):
        return (
            False,
            {},
            f"HTTP {response.status_code}，返回格式异常",
        )

    message = str(
        payload.get("message") or ""
    ).strip()

    if response.status_code >= 400:
        return (
            False,
            payload,
            message or f"HTTP {response.status_code}",
        )

    if not payload.get("success", False):
        return (
            False,
            payload,
            message or "接口返回失败",
        )

    data = payload.get("data")

    if not isinstance(data, dict):
        data = {}

    return True, data, message


def get_user_info(
    session: requests.Session,
) -> tuple[bool, dict[str, Any], str]:
    """获取账号信息和余额。"""
    return request_json(
        session,
        "GET",
        "/api/user/self",
    )


def get_checkin_status(
    session: requests.Session,
) -> tuple[bool, dict[str, Any], str]:
    """查询今日签到状态。"""
    return request_json(
        session,
        "GET",
        "/api/user/checkin",
    )


def do_checkin(
    session: requests.Session,
) -> tuple[bool, dict[str, Any], str]:
    """执行签到。"""
    ok, data, message = request_json(
        session,
        "POST",
        "/api/user/checkin",
    )

    # 并发或重复调用时，站点可能返回“今日已签到”
    if not ok and "今日已签到" in message:
        return (
            True,
            {"already_checked_in": True},
            message,
        )

    return ok, data, message


def get_current_session_cookie(
    session: requests.Session,
) -> str:
    """
    获取请求完成后的 Session Cookie。

    如果服务端通过 Set-Cookie 刷新 Session，
    这里会取到更新后的值。
    """
    candidates = []

    for cookie in session.cookies:
        if cookie.name == "session":
            candidates.append(cookie)

    if not candidates:
        return ""

    for cookie in candidates:
        if cookie.domain in {
            "gorouter.app",
            ".gorouter.app",
        }:
            return str(cookie.value or "")

    return str(candidates[-1].value or "")


def run_account(
    account: dict[str, Any],
    index: int,
    total: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """执行单个账号签到。"""
    name = account["name"]

    log.info("-" * 48)
    log.info(
        "[%d/%d] 开始处理：%s",
        index,
        total,
        name,
    )

    result: dict[str, Any] = {
        "name": name,
        "success": False,
        "status": "failed",
        "message": "",
        "reward_usd": 0.0,
        "balance_usd": 0.0,
        "username": "",
    }

    # 保留原账号中的其他自定义字段
    updated_account = dict(account)

    session = create_session(account)

    # 验证登录并获取当前余额
    ok, user_info, message = get_user_info(session)

    if not ok:
        result["message"] = (
            f"登录验证失败：{message}"
        )

        log.error(
            "%s：%s",
            name,
            result["message"],
        )

        return result, updated_account

    display_name = (
        user_info.get("display_name")
        or user_info.get("username")
        or user_info.get("email")
        or ""
    )

    result["username"] = mask_name(
        str(display_name)
    )

    result["balance_usd"] = quota_to_usd(
        user_info.get("quota")
    )

    log.info(
        "%s：登录成功，当前余额 $%.2f",
        name,
        result["balance_usd"],
    )

    # 查询签到状态
    ok, checkin_data, message = get_checkin_status(
        session
    )

    if not ok:
        result["message"] = (
            f"查询签到状态失败：{message}"
        )

        log.error(
            "%s：%s",
            name,
            result["message"],
        )

        updated_session = get_current_session_cookie(
            session
        )

        if updated_session:
            updated_account["session"] = updated_session

        return result, updated_account

    stats = checkin_data.get("stats")

    if not isinstance(stats, dict):
        stats = {}

    checked_in_today = bool(
        stats.get("checked_in_today")
    )

    if checked_in_today:
        result["success"] = True
        result["status"] = "already"
        result["message"] = "今日已签到"

        # 尝试读取最近一次签到奖励
        records = stats.get("records")

        if isinstance(records, list) and records:
            last_record = records[-1]

            if isinstance(last_record, dict):
                result["reward_usd"] = quota_to_usd(
                    last_record.get("quota_awarded")
                )

        log.info(
            "%s：今日已签到",
            name,
        )

    else:
        # 今日尚未签到，执行签到
        ok, checkin_result, message = do_checkin(
            session
        )

        if not ok:
            result["message"] = (
                f"签到失败：{message}"
            )

            log.error(
                "%s：%s",
                name,
                result["message"],
            )

            updated_session = get_current_session_cookie(
                session
            )

            if updated_session:
                updated_account["session"] = (
                    updated_session
                )

            return result, updated_account

        if checkin_result.get(
            "already_checked_in"
        ):
            result["success"] = True
            result["status"] = "already"
            result["message"] = "今日已签到"

            log.info(
                "%s：接口返回今日已签到",
                name,
            )

        else:
            result["success"] = True
            result["status"] = "checked"
            result["message"] = "签到成功"

            result["reward_usd"] = quota_to_usd(
                checkin_result.get("quota_awarded")
            )

            log.info(
                "%s：签到成功，获得 $%.2f",
                name,
                result["reward_usd"],
            )

        # 签到后重新查询余额
        refreshed, refreshed_info, _ = get_user_info(
            session
        )

        if refreshed:
            result["balance_usd"] = quota_to_usd(
                refreshed_info.get("quota")
            )

            log.info(
                "%s：签到后余额 $%.2f",
                name,
                result["balance_usd"],
            )

    # 保存服务端可能刷新的 Session
    updated_session = get_current_session_cookie(
        session
    )

    if updated_session:
        updated_account["session"] = updated_session

    return result, updated_account


def save_updated_accounts(
    accounts: list[dict[str, Any]],
) -> None:
    """保存更新后的多账号 JSON。"""
    with open(
        "accounts.updated.json",
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            accounts,
            file,
            ensure_ascii=False,
            indent=2,
        )

    log.info(
        "已生成 accounts.updated.json，"
        "供工作流可选写回 Secret"
    )


def send_notification(
    results: list[dict[str, Any]],
) -> None:
    """发送 Telegram 汇总通知。"""
    try:
        from notify import send_tg_notification

        send_tg_notification(
            results,
            today_text(),
        )

    except ImportError as exc:
        log.warning(
            "无法导入 notify 模块：%s",
            exc,
        )

    except Exception as exc:
        log.error(
            "发送 Telegram 通知异常：%s",
            exc,
        )


def main() -> int:
    log.info("=" * 48)
    log.info("GoRouter 多账号每日签到启动")
    log.info("目标站点：%s", BASE_URL)

    try:
        accounts = load_accounts()
    except ValueError as exc:
        log.error("%s", exc)
        return 1

    log.info(
        "已载入 %d 个有效账号",
        len(accounts),
    )

    try:
        interval = max(
            0.0,
            float(
                os.getenv(
                    "CHECKIN_INTERVAL_SECONDS",
                    "1.5",
                )
            ),
        )
    except ValueError:
        interval = 1.5

    results: list[dict[str, Any]] = []
    updated_accounts: list[dict[str, Any]] = []

    for index, account in enumerate(
        accounts,
        start=1,
    ):
        result, updated_account = run_account(
            account,
            index,
            len(accounts),
        )

        results.append(result)
        updated_accounts.append(updated_account)

        if (
            index < len(accounts)
            and interval > 0
        ):
            time.sleep(interval)

    # 无论部分账号是否失败，都保存其余账号状态
    save_updated_accounts(updated_accounts)

    # Telegram 汇总
    send_notification(results)

    success_count = sum(
        1
        for item in results
        if item.get("success")
    )

    failed_count = (
        len(results) - success_count
    )

    log.info("-" * 48)
    log.info(
        "签到完成：成功 %d，失败 %d，总计 %d",
        success_count,
        failed_count,
        len(results),
    )
    log.info("=" * 48)

    # 部分账号失败时不让任务整体失败
    # 只有全部账号都失败才返回非零状态
    return 0 if success_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
