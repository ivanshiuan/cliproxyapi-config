"""Send a single test LINE push to verify the channel access token works.

Usage:
    .venv/bin/python scripts/line_check.py <line_user_id> ["custom message"]

Prereq: LINE_CHANNEL_ACCESS_TOKEN set in the environment or .env.
Get your own line_user_id by adding the official account as a friend and
reading the webhook event, or from the LINE Developers console test tools.
"""

from __future__ import annotations

import asyncio
import sys

from restaurant_api.config import get_settings
from restaurant_api.integrations.line import (
    HttpLineMessenger,
    LineApiError,
    LineMessage,
)


async def _main() -> int:
    if len(sys.argv) < 2:
        print("用法：python scripts/line_check.py <line_user_id> [訊息]")
        return 2

    user_id = sys.argv[1]
    text = sys.argv[2] if len(sys.argv) > 2 else "✅ LINE 推播測試成功！這是來自餐飲系統的測試訊息。"

    token = get_settings().line_channel_access_token
    if not token:
        print("❌ 找不到 LINE_CHANNEL_ACCESS_TOKEN。請先在 .env 或環境變數設定後再試。")
        return 1

    messenger = HttpLineMessenger.from_env()
    try:
        await messenger.push(user_id, LineMessage(kind="text", text=text))
        print(f"✅ 已送出測試訊息給 {user_id}。請到 LINE 確認是否收到。")
        return 0
    except LineApiError as exc:
        print(f"❌ LINE API 回傳錯誤：HTTP {exc.status}")
        print(f"   詳情：{exc.body[:500]}")
        print("   常見原因：token 錯誤/過期、user_id 不對、或對方未加官方帳號好友。")
        return 1
    finally:
        await messenger.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
