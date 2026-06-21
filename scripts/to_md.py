#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["markitdown[all]"]
# ///
"""把任意檔案轉成 Markdown，給 Claude 讀（省 token）。

為什麼存在
----------
Claude Code 的原生 `Read` 已能直接讀 PDF / 圖片 / CSV / JSON。但遇到
**大型 PDF、Office 檔（docx/pptx/xlsx）、EPub、複雜 HTML 表格**時，先轉成
精簡 Markdown 再讀，可大幅省 token、結構更乾淨。這支腳本就是那個「先轉一手」。

設計原則（呼應 CLAUDE.md 不變法則）
----------------------------------
- **零依賴污染**：markitdown 不進 pyproject.toml。本檔用 PEP 723 inline metadata，
  由 `uv run` 起一個快取的臨時環境執行，碰不到 .venv / production stack。
- **離線優先**：預設不開 LLM/OCR，不需要 API key、不對外打 LLM。
  真要 OCR 掃描檔再加 --llm（需 OPENAI_API_KEY，另議）。
- **可稽核**：只做「檔案 → 文字」轉換，不解讀、不碰金額。任何要入帳的數字，
  仍須走 restaurant_api 的結構化驗證 + 人工覆核，別信這支腳本吐的純文字。

用法
----
    uv run scripts/to_md.py 某發票.pdf                 # 轉到 <input>.md 旁邊
    uv run scripts/to_md.py report.docx -o out.md      # 指定輸出
    uv run scripts/to_md.py a.pdf b.xlsx c.pptx        # 批次
    uv run scripts/to_md.py big.pdf --stdout           # 直接吐到 stdout

    # 或透過 Makefile：
    make to-md FILE=某發票.pdf

退出碼：全部成功 0；任一失敗 1。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _convert_one(md, src: Path) -> str:
    """轉一個檔，回傳 Markdown 文字。例外往外丟，由 main 收。"""
    result = md.convert(str(src))
    return result.text_content or ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert files to Markdown for token-efficient reading.",
    )
    parser.add_argument("files", nargs="+", help="輸入檔（PDF/docx/pptx/xlsx/epub/html/...）")
    parser.add_argument(
        "-o",
        "--out",
        help="輸出路徑（單檔時用）；省略則寫到 <input>.md",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="把 Markdown 印到 stdout 而非寫檔",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="啟用 LLM 圖片描述/OCR（需 OPENAI_API_KEY + 模型，預設關閉）",
    )
    parser.add_argument(
        "--llm-model",
        default="gpt-4o",
        help="--llm 開啟時用的模型（預設 gpt-4o）",
    )
    args = parser.parse_args(argv)

    if args.out and len(args.files) > 1:
        print("error: --out 只能搭配單一輸入檔", file=sys.stderr)
        return 1

    if args.out and args.stdout:
        print("error: --out 與 --stdout 互斥（--stdout 模式不寫檔）", file=sys.stderr)
        return 1

    try:
        from markitdown import MarkItDown
    except ImportError:  # pragma: no cover - 只在沒裝時觸發
        print(
            "error: markitdown 未安裝。請用 `uv run scripts/to_md.py ...`（會自動取得），"
            "不要 pip install 進 .venv。",
            file=sys.stderr,
        )
        return 1

    md_kwargs: dict = {}
    if args.llm:
        import os

        if not os.getenv("OPENAI_API_KEY"):
            print("error: --llm 需要環境變數 OPENAI_API_KEY", file=sys.stderr)
            return 1
        from openai import OpenAI  # type: ignore[import-not-found]

        md_kwargs["llm_client"] = OpenAI()
        md_kwargs["llm_model"] = args.llm_model

    md = MarkItDown(**md_kwargs)

    had_error = False
    for raw in args.files:
        src = Path(raw)
        if not src.is_file():
            print(f"skip: 找不到檔案 {src}", file=sys.stderr)
            had_error = True
            continue

        try:
            text = _convert_one(md, src)
        except Exception as exc:  # CLI 邊界，回報所有失敗
            print(f"fail: {src} -> {type(exc).__name__}: {exc}", file=sys.stderr)
            had_error = True
            continue

        if args.stdout:
            sys.stdout.write(text)
            if not text.endswith("\n"):
                sys.stdout.write("\n")
            continue

        out = Path(args.out) if args.out else src.with_suffix(src.suffix + ".md")
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")

            # 粗估省了多少：原始 bytes vs markdown chars（~4 char/token）
            src_bytes = src.stat().st_size
            md_chars = len(text)
            est_tokens = md_chars // 4
            print(
                f"ok: {src.name} -> {out}  "
                f"({src_bytes:,} bytes 原檔 → {md_chars:,} chars markdown, ~{est_tokens:,} tokens)",
                file=sys.stderr,
            )
        except Exception as exc:  # 寫檔/取狀態失敗只算這一檔，不中斷整批
            print(f"fail: {src} -> {type(exc).__name__}: {exc}", file=sys.stderr)
            had_error = True
            continue

    return 1 if had_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
