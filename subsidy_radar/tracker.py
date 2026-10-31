"""申請追蹤 — data/subsidies/applications.yaml 的讀寫與進度計算。

這個檔給人看也給人改：每案列出應備文件與狀態（todo / done / na）。
CLI 的 apply / doc 指令只是幫忙改這個檔。雲端 session 是拋棄式的，
追蹤狀態要 commit 進 git 才會留下來。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from subsidy_radar.models import DocStatus, SubsidyApplication, SubsidyProgram, taipei_today

_HEADER = """\
# 補助申請追蹤 — `python -m subsidy_radar apply / doc` 會改這個檔，也可以直接手改
# 文件狀態：todo = 未備、done = 已備、na = 不適用
"""


def load_applications(path: Path) -> dict[str, SubsidyApplication]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        program_id: SubsidyApplication.model_validate({"program_id": program_id, **fields})
        for program_id, fields in (raw.get("applications") or {}).items()
    }


def save_applications(path: Path, applications: dict[str, SubsidyApplication]) -> None:
    data = {
        "applications": {
            program_id: application.model_dump(
                mode="json", exclude={"program_id"}, exclude_none=True
            )
            for program_id, application in sorted(applications.items())
        }
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    path.write_text(_HEADER + body, encoding="utf-8")


def start_application(program: SubsidyProgram, today: date | None = None) -> SubsidyApplication:
    return SubsidyApplication(
        program_id=program.id,
        started=today or taipei_today(),
        documents={doc: DocStatus.todo for doc in program.required_documents},
    )


def set_document(
    application: SubsidyApplication, document: str, status: DocStatus
) -> SubsidyApplication:
    """更新（或新增）一份文件的狀態，回傳新的追蹤紀錄。"""
    return application.model_copy(
        update={"documents": {**application.documents, document: status}}
    )


def resolve_document(application: SubsidyApplication, ref: str) -> str:
    """ref 可以是 status 列出的 1 起算編號，或文件全名；查無則視為新文件名。"""
    names = list(application.documents)
    if ref.isdigit() and 1 <= int(ref) <= len(names):
        return names[int(ref) - 1]
    return ref


def missing_documents(application: SubsidyApplication) -> list[str]:
    return [doc for doc, status in application.documents.items() if status == DocStatus.todo]


def progress(application: SubsidyApplication) -> tuple[int, int]:
    """（已備, 需備），不適用的文件不計。"""
    needed = [s for s in application.documents.values() if s != DocStatus.na]
    return sum(1 for s in needed if s == DocStatus.done), len(needed)
