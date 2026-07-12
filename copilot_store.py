"""Durable, owner-scoped storage for Fontify Copilot documents.

The root document intentionally contains metadata only. Blocks and rendered
pages live in subcollections so a long document cannot hit Firestore's 1 MiB
document limit. All state transitions go through one optimistic transaction.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

from firebase_admin import firestore
from google.cloud.firestore_v1 import Query
from google.cloud.firestore_v1.transaction import Transaction


MAX_BLOCKS = 120
MAX_PAGES = 20
MAX_HISTORY = 30
MAX_SUBDOCUMENT_BYTES = 850_000
MAX_SETTINGS_BYTES = 96_000


class CopilotStoreError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class VersionConflictError(CopilotStoreError):
    def __init__(self):
        super().__init__(
            "Belge başka bir cihazda veya sekmede değiştirildi. Lütfen sayfayı yenileyin.",
            409,
        )


def _db():
    return firestore.client()


def _json_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise CopilotStoreError("Belge verisi kaydedilebilir bir JSON biçiminde değil.") from exc


def _ensure_small(value: Any, label: str, maximum: int) -> None:
    if _json_size(value) > maximum:
        raise CopilotStoreError(f"{label} Firestore sınırını aşıyor.", 413)


def _validate_state(layout: dict, blocks: list, page_settings: Optional[dict] = None) -> None:
    if not isinstance(layout, dict) or not isinstance(blocks, list):
        raise CopilotStoreError("Copilot belge durumu geçersiz.")
    pages = layout.get("pages")
    if not isinstance(pages, list):
        raise CopilotStoreError("Belge sayfaları geçersiz.")
    if len(blocks) > MAX_BLOCKS:
        raise CopilotStoreError(f"En fazla {MAX_BLOCKS} blok kaydedilebilir.", 413)
    if len(pages) > MAX_PAGES:
        raise CopilotStoreError(f"En fazla {MAX_PAGES} sayfa kaydedilebilir.", 413)
    for block in blocks:
        if not isinstance(block, dict):
            raise CopilotStoreError("Belge bloğu geçersiz.")
        _ensure_small(block, "Bir belge bloğu", MAX_SUBDOCUMENT_BYTES)
    for page in pages:
        if not isinstance(page, dict):
            raise CopilotStoreError("Belge sayfası geçersiz.")
        _ensure_small(page, "Bir belge sayfası", MAX_SUBDOCUMENT_BYTES)
    _ensure_small(layout.get("settings", {}), "Belge ayarları", MAX_SETTINGS_BYTES)
    if page_settings is not None:
        if not isinstance(page_settings, dict):
            raise CopilotStoreError("Sayfa ayarları geçersiz.")
        _ensure_small(page_settings, "Sayfa ayarları", MAX_SETTINGS_BYTES)


def _layout_meta(layout: dict) -> dict:
    return {
        key: layout[key]
        for key in ("page_size", "page_width", "page_height", "px_per_mm")
        if key in layout
    }


def _document_ref(document_id: str):
    return _db().collection("ai_copilot_documents").document(document_id)


def _load_document_state(doc_ref, doc_data: dict) -> dict:
    blocks = [
        snapshot.to_dict().get("data")
        for snapshot in doc_ref.collection("blocks").order_by("index").stream()
    ]
    pages = [
        snapshot.to_dict().get("data")
        for snapshot in doc_ref.collection("pages").order_by("index").stream()
    ]
    history = [
        snapshot.to_dict()
        for snapshot in doc_ref.collection("history").order_by("sequence").stream()
    ]
    redo_stack = [
        snapshot.to_dict()
        for snapshot in doc_ref.collection("redo").order_by("sequence").stream()
    ]
    metadata = dict(doc_data.get("layout_meta") or {})
    metadata.update({
        "version": doc_data.get("version", 1),
        "settings": dict(doc_data.get("layout_settings") or doc_data.get("page_settings") or {}),
        "pages": pages,
    })
    doc_data["layout"] = metadata
    doc_data["blocks"] = blocks
    doc_data["history"] = history
    doc_data["redo_stack"] = redo_stack
    return doc_data


def create_document(
    *,
    user_id: str,
    font_id: str,
    secondary_font_id: str,
    page_settings: dict,
    version: int,
    layout: dict,
    blocks: list,
) -> str:
    _validate_state(layout, blocks, page_settings)
    document_id = str(uuid.uuid4())
    now = time.time()
    db = _db()
    doc_ref = db.collection("ai_copilot_documents").document(document_id)
    batch = db.batch()
    batch.set(doc_ref, {
        "user_id": user_id,
        "version": int(version),
        "font_id": font_id,
        "secondary_font_id": secondary_font_id,
        "page_settings": dict(page_settings),
        "layout_settings": dict(layout.get("settings") or {}),
        "layout_meta": _layout_meta(layout),
        "created_at": now,
        "updated_at": now,
        "last_accessed_at": now,
        "status": "active",
        "schema_version": 2,
    })
    for index, block in enumerate(blocks):
        batch.set(doc_ref.collection("blocks").document(str(index)), {"index": index, "data": block})
    for index, page in enumerate(layout.get("pages", [])):
        batch.set(doc_ref.collection("pages").document(str(index)), {"index": index, "data": page})
    batch.commit()
    return document_id


def _owned_document(document_id: str, user_id: str) -> tuple[Any, dict]:
    doc_ref = _document_ref(document_id)
    snapshot = doc_ref.get()
    if not snapshot.exists:
        raise CopilotStoreError("Belge bulunamadı.", 404)
    data = snapshot.to_dict()
    if data.get("user_id") != user_id:
        raise CopilotStoreError("Bu belgeye erişim yetkiniz yok.", 403)
    data["id"] = document_id
    return doc_ref, data


def get_document(document_id: str, user_id: str) -> dict:
    doc_ref, data = _owned_document(document_id, user_id)
    doc_ref.update({"last_accessed_at": time.time()})
    return _load_document_state(doc_ref, data)


def get_latest_document(user_id: str) -> Optional[dict]:
    query = (
        _db().collection("ai_copilot_documents")
        .where("user_id", "==", user_id)
        .where("status", "==", "active")
        .order_by("updated_at", direction=Query.DESCENDING)
        .limit(1)
    )
    snapshot = next(iter(query.stream()), None)
    if snapshot is None:
        return None
    doc_ref = snapshot.reference
    data = snapshot.to_dict()
    data["id"] = snapshot.id
    doc_ref.update({"last_accessed_at": time.time()})
    return _load_document_state(doc_ref, data)


def _bounded_snapshots(collection_ref, *, transaction: Transaction) -> list:
    return list(
        collection_ref.order_by("sequence", direction=Query.ASCENDING)
        .limit(MAX_HISTORY)
        .stream(transaction=transaction)
    )


@firestore.transactional
def _update_document_txn(
    transaction: Transaction,
    doc_ref,
    user_id: str,
    expected_version: int,
    new_layout: dict,
    new_blocks: list,
    record: Optional[dict] = None,
    page_settings: Optional[dict] = None,
    *,
    is_undo: bool = False,
    is_redo: bool = False,
    redo_record: Optional[dict] = None,
) -> int:
    # Firestore requires all reads to finish before transaction writes. Read the
    # root and bounded history stacks first; all child-state writes are then
    # deterministic numeric document IDs.
    doc_snapshot = doc_ref.get(transaction=transaction)
    if not doc_snapshot.exists:
        raise CopilotStoreError("Belge bulunamadı.", 404)
    current = doc_snapshot.to_dict()
    if current.get("user_id") != user_id:
        raise CopilotStoreError("Bu belgeye erişim yetkiniz yok.", 403)
    if int(current.get("version", 0)) != int(expected_version):
        raise VersionConflictError()

    history_snapshots = _bounded_snapshots(doc_ref.collection("history"), transaction=transaction)
    redo_snapshots = _bounded_snapshots(doc_ref.collection("redo"), transaction=transaction)

    try:
        new_version = int(new_layout.get("version", int(expected_version) + 1))
    except (TypeError, ValueError) as exc:
        raise CopilotStoreError("Belge sürümü geçersiz.") from exc
    if new_version != int(expected_version) + 1:
        raise VersionConflictError()

    now = time.time()
    root_update = {
        "version": new_version,
        "updated_at": now,
        "last_accessed_at": now,
        "layout_settings": dict(new_layout.get("settings") or {}),
        "layout_meta": _layout_meta(new_layout),
    }
    if page_settings is not None:
        root_update["page_settings"] = dict(page_settings)
    transaction.update(doc_ref, root_update)

    pages = new_layout.get("pages", [])
    for index in range(MAX_BLOCKS):
        ref = doc_ref.collection("blocks").document(str(index))
        if index < len(new_blocks):
            transaction.set(ref, {"index": index, "data": new_blocks[index]})
        else:
            transaction.delete(ref)
    for index in range(MAX_PAGES):
        ref = doc_ref.collection("pages").document(str(index))
        if index < len(pages):
            transaction.set(ref, {"index": index, "data": pages[index]})
        else:
            transaction.delete(ref)

    if is_undo:
        if not history_snapshots or not redo_record:
            raise CopilotStoreError("Geri alınacak işlem yok.", 400)
        transaction.delete(history_snapshots[-1].reference)
        if len(redo_snapshots) >= MAX_HISTORY:
            transaction.delete(redo_snapshots[0].reference)
        stored_redo = dict(redo_record)
        stored_redo.update({"sequence": new_version, "created_at": now})
        transaction.set(doc_ref.collection("redo").document(str(new_version)), stored_redo)
    elif is_redo:
        if not redo_snapshots or not record:
            raise CopilotStoreError("İleri alınacak işlem yok.", 400)
        transaction.delete(redo_snapshots[-1].reference)
        if len(history_snapshots) >= MAX_HISTORY:
            transaction.delete(history_snapshots[0].reference)
        stored_history = dict(record)
        stored_history.update({"sequence": new_version, "created_at": now})
        transaction.set(doc_ref.collection("history").document(str(new_version)), stored_history)
    elif record:
        for snapshot in redo_snapshots:
            transaction.delete(snapshot.reference)
        if len(history_snapshots) >= MAX_HISTORY:
            transaction.delete(history_snapshots[0].reference)
        stored_history = dict(record)
        stored_history.update({"sequence": new_version, "created_at": now})
        transaction.set(doc_ref.collection("history").document(str(new_version)), stored_history)

    return new_version


def update_document(
    document_id: str,
    user_id: str,
    expected_version: int,
    new_layout: dict,
    new_blocks: list,
    record: Optional[dict] = None,
    page_settings: Optional[dict] = None,
) -> int:
    _validate_state(new_layout, new_blocks, page_settings)
    if record is not None:
        _ensure_small(record, "İşlem geçmişi", MAX_SUBDOCUMENT_BYTES)
    return _update_document_txn(
        _db().transaction(),
        _document_ref(document_id),
        user_id,
        expected_version,
        new_layout,
        new_blocks,
        record,
        page_settings,
    )


def undo_document(
    document_id: str,
    user_id: str,
    expected_version: int,
    new_layout: dict,
    new_blocks: list,
    redo_record: dict,
) -> int:
    _validate_state(new_layout, new_blocks)
    _ensure_small(redo_record, "Geri alma geçmişi", MAX_SUBDOCUMENT_BYTES)
    return _update_document_txn(
        _db().transaction(),
        _document_ref(document_id),
        user_id,
        expected_version,
        new_layout,
        new_blocks,
        is_undo=True,
        redo_record=redo_record,
    )


def redo_document(
    document_id: str,
    user_id: str,
    expected_version: int,
    new_layout: dict,
    new_blocks: list,
    history_record: dict,
) -> int:
    _validate_state(new_layout, new_blocks)
    _ensure_small(history_record, "İleri alma geçmişi", MAX_SUBDOCUMENT_BYTES)
    return _update_document_txn(
        _db().transaction(),
        _document_ref(document_id),
        user_id,
        expected_version,
        new_layout,
        new_blocks,
        record=history_record,
        is_redo=True,
    )
