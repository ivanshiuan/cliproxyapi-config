"""Claude API wrapper for AI content generation (Claude creative layer).

Falls back to deterministic stub content when ANTHROPIC_API_KEY is absent so
the service works in CI and local dev without credentials.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..config import get_settings

logger = logging.getLogger("restaurant_api.services.ai_content")

_STUB_DISCLAIMER = "[AI stub — set ANTHROPIC_API_KEY to enable real generation]"


@dataclass(frozen=True)
class GenerationResult:
    content: str
    model: str
    input_tokens: int | None
    output_tokens: int | None


def _build_system_prompt(language: str, tone: str | None) -> str:
    tone_clause = f"語氣風格：{tone}。" if tone else ""
    return (
        f"你是一位台灣餐飲業行銷專家，擅長撰寫吸引顧客的行銷文案。"
        f"輸出語言：{language}。{tone_clause}"
        f"回覆只包含文案本身，不需要解釋或標題。"
    )


async def generate_content(
    prompt: str,
    memory_context: str,
    asset_type: str,
    platform: str,
    language: str = "zh-TW",
    tone: str | None = None,
) -> GenerationResult:
    """Generate marketing content via Claude API.

    If ANTHROPIC_API_KEY is not configured, returns a clearly-labelled stub
    so CI passes and developers can test the full flow without credentials.
    """
    settings = get_settings()

    if not settings.anthropic_api_key:
        logger.info("ai_content.stub_mode", extra={"reason": "no_api_key"})
        stub = (
            f"{_STUB_DISCLAIMER}\n\n"
            f"[{asset_type} / {platform}]\n"
            f"行銷目標：{prompt}\n\n"
            f"✨ 限時優惠！來店消費享點數回饋，立即體驗美食新享受！"
        )
        return GenerationResult(
            content=stub,
            model="stub",
            input_tokens=None,
            output_tokens=None,
        )

    try:
        import anthropic  # deferred — not required when stubbing

        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

        user_msg = (
            f"品牌記憶與風格參考：\n{memory_context}\n\n"
            f"請為以下需求撰寫{asset_type}文案（平台：{platform}）：\n{prompt}"
        )

        response = await client.messages.create(
            model=settings.ai_content_model,
            max_tokens=settings.ai_content_max_tokens,
            system=_build_system_prompt(language, tone),
            messages=[{"role": "user", "content": user_msg}],
        )

        content = response.content[0].text if response.content else ""
        logger.info(
            "ai_content.generated",
            extra={
                "model": response.model,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        )
        return GenerationResult(
            content=content,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    except Exception as exc:
        logger.error("ai_content.generation_failed", extra={"error": str(exc)})
        raise


async def generate_strategy(
    objective: str,
    context: str | None,
    memory_context: str,
) -> GenerationResult:
    """Ask Claude to produce a structured marketing strategy brief."""
    settings = get_settings()

    if not settings.anthropic_api_key:
        logger.info("ai_content.stub_strategy", extra={"reason": "no_api_key"})
        stub = (
            f"{_STUB_DISCLAIMER}\n\n"
            f"## 行銷策略建議\n\n"
            f"**目標**：{objective}\n\n"
            f"**建議行動**：\n"
            f"1. 針對忠實顧客推送專屬優惠（LINE 推播）\n"
            f"2. 利用 UGC 打卡換點數提升 IG 曝光\n"
            f"3. 設計裂變邀請碼擴大新客來源\n"
            f"4. 分析 RFM 數據找出高潛力沉睡客戶並喚醒\n\n"
            f"**預期成效**：回訪率 +15%、新客轉換率 +8%"
        )
        return GenerationResult(
            content=stub,
            model="stub",
            input_tokens=None,
            output_tokens=None,
        )

    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

        context_clause = f"\n\n額外背景：{context}" if context else ""
        user_msg = (
            f"品牌記憶與過往經驗：\n{memory_context}\n\n"
            f"行銷目標：{objective}{context_clause}\n\n"
            f"請提供一份具體可行的行銷策略簡報，包含：目標受眾、核心訊息、"
            f"執行步驟、建議素材類型、預期效果指標。"
        )

        response = await client.messages.create(
            model=settings.ai_content_model,
            max_tokens=min(settings.ai_content_max_tokens * 2, 4096),
            system=(
                "你是台灣餐飲業行銷顧問，熟悉 LINE 生態圈、RFM 分眾、"
                "UGC 行銷與會員經濟。輸出使用繁體中文，以 Markdown 格式呈現。"
            ),
            messages=[{"role": "user", "content": user_msg}],
        )

        content = response.content[0].text if response.content else ""
        return GenerationResult(
            content=content,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    except Exception as exc:
        logger.error("ai_content.strategy_failed", extra={"error": str(exc)})
        raise


__all__ = ["GenerationResult", "generate_content", "generate_strategy"]
