#!/usr/bin/env python3
"""
GoRouter 多账号 Telegram 汇总通知。
"""

from __future__ import annotations

import logging
import os
from html import escape
from typing import Any

import requests


TG_BOT_TOKEN = os.getenv(
    "TG_BOT_TOKEN",
    "",
).strip()

TG_CHAT_ID = os.getenv(
    "TG_CHAT_ID",
    "",
).strip()

REQUEST_TIMEOUT = 20

# Telegram 单条消息上限约 4096 字符，
# 这里预留一部分安全空间。
TELEGRAM_TEXT_LIMIT = 3900


log = logging.getLogger("gorouter-notify")


def money(value: Any) -> str:
    """格式化美元金额。"""
    try:
        return f"${float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def build_result_line(
    item: dict[str, Any],
) -> str:
    """构建单个账号通知内容。"""
    name = escape(
        str(item.get("name") or "未命名账号")
    )

    balance = money(
        item.get("balance_usd")
    )

    reward = money(
        item.get("reward_usd")
    )

    status = str(
        item.get("status") or ""
    )

    success = bool(
        item.get("success")
    )

    if success and status == "checked":
        return (
            f"🎉 <b>{name}</b>："
            f"签到成功，获得 {reward}"
            f"\n   💰 余额：{balance}"
        )

    if success and status == "already":
        return (
            f"✅ <b>{name}</b>："
            f"今日已签到"
            f"\n   💰 余额：{balance}"
        )

    message = escape(
        str(
            item.get("message")
            or "未知错误"
        )
    )

    return (
        f"❌ <b>{name}</b>：{message}"
    )


def split_messages(
    header: str,
    lines: list[str],
    footer: str,
) -> list[str]:
    """
    消息过长时自动拆分。

    正常几个账号只会发送一条。
    """
    messages: list[str] = []
    current = header

    for line in lines:
        candidate = (
            f"{current}\n\n{line}"
        )

        if (
            len(candidate)
            + len(footer)
            + 2
            > TELEGRAM_TEXT_LIMIT
        ):
            messages.append(
                f"{current}\n\n{footer}"
            )

            current = (
                header
                + "\n\n"
                + line
            )

        else:
            current = candidate

    messages.append(
        f"{current}\n\n{footer}"
    )

    return messages


def send_one_message(
    text: str,
) -> bool:
    """发送一条 Telegram 消息。"""
    url = (
        "https://api.telegram.org/"
        f"bot{TG_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        result = response.json()

    except requests.RequestException as exc:
        log.error(
            "Telegram 请求异常：%s",
            exc,
        )
        return False

    except ValueError:
        log.error(
            "Telegram 返回内容不是 JSON"
        )
        return False

    if result.get("ok"):
        return True

    log.warning(
        "Telegram 通知发送失败：%s",
        result.get(
            "description",
            "未知错误",
        ),
    )

    return False


def send_tg_notification(
    results: list[dict[str, Any]],
    date_text: str,
) -> bool:
    """发送多账号签到汇总通知。"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log.info(
            "未配置 TG_BOT_TOKEN 或 "
            "TG_CHAT_ID，跳过通知"
        )
        return False

    success_count = sum(
        1
        for item in results
        if item.get("success")
    )

    failed_count = (
        len(results) - success_count
    )

    header = (
        "<b>GoRouter 多账号签到</b>"
        f"\n📅 {escape(date_text)}"
    )

    lines = [
        build_result_line(item)
        for item in results
    ]

    footer = (
        "----------------"
        f"\n成功：<b>{success_count}</b>"
        f"　失败：<b>{failed_count}</b>"
        f"　总计：<b>{len(results)}</b>"
    )

    messages = split_messages(
        header,
        lines,
        footer,
    )

    all_ok = True

    for text in messages:
        if not send_one_message(text):
            all_ok = False

    if all_ok:
        log.info(
            "Telegram 汇总通知发送成功"
        )

    return all_ok
