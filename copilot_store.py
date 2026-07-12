import time
import uuid
from typing import Tuple, List, Dict, Any, Optional
from firebase_admin import firestore
from google.cloud.firestore_v1.transaction import Transaction

class CopilotStoreError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code

class VersionConflictError(CopilotStoreError):
    def __init__(self):
        super().__init__("Belge başka bir cihazda veya sekmede değiştirilmiş. Lütfen sayfayı yenileyin.", 409)

def _db():
    return firestore.client()

def _delete_collection(coll_ref, batch_size=100):
    docs = coll_ref.limit(batch_size).stream()
    deleted = 0
    for doc in docs:
        doc.reference.delete()
        deleted += 1
    if deleted >= batch_size:
        return _delete_collection(coll_ref, batch_size)

def create_document(user_id: str, font_id: str, secondary_font_id: str, page_settings: dict, version: int, layout: dict, blocks: list) -> str:
    document_id = str(uuid.uuid4())
    now = time.time()
    
    batch = _db().batch()
    doc_ref = _db().collection('ai_copilot_documents').document(document_id)
    
    doc_data = {
        "user_id": user_id,
        "version": version,
        "font_id": font_id,
        "secondary_font_id": secondary_font_id,
        "page_settings": page_settings,
        "created_at": now,
        "updated_at": now,
        "last_accessed_at": now,
        "status": "active",
        "schema_version": 1
    }
    batch.set(doc_ref, doc_data)
    
    for i, block in enumerate(blocks):
        block_ref = doc_ref.collection('blocks').document(str(i))
        batch.set(block_ref, {"index": i, "data": block})
        
    for i, page in enumerate(layout.get('pages', [])):
        page_ref = doc_ref.collection('pages').document(str(i))
        batch.set(page_ref, {"index": i, "data": page})
        
    batch.commit()
    return document_id

def get_document(document_id: str, user_id: str) -> dict:
    doc_ref = _db().collection('ai_copilot_documents').document(document_id)
    doc_snap = doc_ref.get()
    
    if not doc_snap.exists:
        raise CopilotStoreError("Belge bulunamadı. Önce kaydedin.", 404)
        
    doc_data = doc_snap.to_dict()
    doc_data["id"] = document_id
    if doc_data.get("user_id") != user_id:
        raise CopilotStoreError("Bu belgeye erişim yetkiniz yok.", 403)
        
    # Update last_accessed_at asynchronously or in a separate batch to avoid locking
    # But since it's just a get, we can fire and forget a set
    doc_ref.update({"last_accessed_at": time.time()})
    
    return _load_document_state(doc_ref, doc_data)

def _load_document_state(doc_ref, doc_data: dict) -> dict:
    # Load blocks
    blocks = []
    blocks_snap = doc_ref.collection('blocks').order_by('index').stream()
    for b in blocks_snap:
        blocks.append(b.to_dict().get('data'))
        
    # Load pages
    pages = []
    pages_snap = doc_ref.collection('pages').order_by('index').stream()
    for p in pages_snap:
        pages.append(p.to_dict().get('data'))
        
    # Load history metadata
    history = []
    history_snap = doc_ref.collection('history').order_by('sequence').stream()
    for h in history_snap:
        history.append(h.to_dict())
        
    redo_stack = []
    redo_snap = doc_ref.collection('redo').order_by('sequence').stream()
    for r in redo_snap:
        redo_stack.append(r.to_dict())
        
    layout = {
        "version": doc_data.get("version"),
        "page_size": "A4",
        "page_width": 2480,
        "page_height": 3508,
        "px_per_mm": 11.809524,
        "settings": doc_data.get("page_settings"),
        "pages": pages
    }
    
    doc_data["layout"] = layout
    doc_data["blocks"] = blocks
    doc_data["history"] = history
    doc_data["redo_stack"] = redo_stack
    return doc_data

def get_latest_document(user_id: str) -> Optional[dict]:
    docs = _db().collection('ai_copilot_documents').where('user_id', '==', user_id).where('status', '==', 'active').order_by('updated_at', direction=firestore.Query.DESCENDING).limit(1).stream()
    for doc in docs:
        doc_ref = doc.reference
        doc_data = doc.to_dict()
        doc_data["id"] = doc.id
        doc_ref.update({"last_accessed_at": time.time()})
        return _load_document_state(doc_ref, doc_data)
    return None

@firestore.transactional
def _update_document_txn(transaction: Transaction, doc_ref, user_id: str, expected_version: int, new_layout: dict, new_blocks: list, record: Optional[dict] = None, page_settings: Optional[dict] = None, is_undo: bool = False, is_redo: bool = False, redo_record: Optional[dict] = None):
    doc_snap = doc_ref.get(transaction=transaction)
    if not doc_snap.exists:
        raise CopilotStoreError("Belge bulunamadı.", 404)
        
    doc_data = doc_snap.to_dict()
    if doc_data.get("user_id") != user_id:
        raise CopilotStoreError("Bu belgeye erişim yetkiniz yok.", 403)
        
    if doc_data.get("version") != expected_version:
        raise VersionConflictError()
        
    new_version = new_layout.get("version", expected_version + 1)
    
    update_data = {
        "version": new_version,
        "updated_at": time.time()
    }
    if page_settings is not None:
        update_data["page_settings"] = page_settings
        
    transaction.update(doc_ref, update_data)
    
    # Overwrite blocks efficiently to save transaction limits
    for i, block in enumerate(new_blocks):
        block_ref = doc_ref.collection('blocks').document(str(i))
        transaction.set(block_ref, {"index": i, "data": block})
        
    # Delete excess blocks
    # Since we can't easily query in transaction for "index >= len", we just try to delete up to a reasonable max (120)
    for i in range(len(new_blocks), 120):
        block_ref = doc_ref.collection('blocks').document(str(i))
        transaction.delete(block_ref)
        
    for i, page in enumerate(new_layout.get('pages', [])):
        page_ref = doc_ref.collection('pages').document(str(i))
        transaction.set(page_ref, {"index": i, "data": page})
        
    # Delete excess pages
    for i in range(len(new_layout.get('pages', [])), 20):
        page_ref = doc_ref.collection('pages').document(str(i))
        transaction.delete(page_ref)

    if record:
        record["sequence"] = new_version
        record["created_at"] = time.time()
        hist_ref = doc_ref.collection('history').document(str(new_version))
        transaction.set(hist_ref, record)
        
        if not is_undo and not is_redo:
            # Clear redo stack
            existing_redo = list(doc_ref.collection('redo').list_documents())
            for r in existing_redo:
                transaction.delete(r)
                
    if is_undo and redo_record:
        # Move last history to redo
        existing_history = list(doc_ref.collection('history').order_by('sequence', direction=firestore.Query.DESCENDING).limit(1).stream(transaction=transaction))
        for h in existing_history:
            transaction.delete(h.reference)
            
        redo_record["sequence"] = new_version
        redo_ref = doc_ref.collection('redo').document(str(new_version))
        transaction.set(redo_ref, redo_record)
        
    if is_redo and record:
        # Move last redo to history
        existing_redo = list(doc_ref.collection('redo').order_by('sequence', direction=firestore.Query.DESCENDING).limit(1).stream(transaction=transaction))
        for r in existing_redo:
            transaction.delete(r.reference)
            
    return new_version

def update_document(document_id: str, user_id: str, expected_version: int, new_layout: dict, new_blocks: list, record: Optional[dict] = None, page_settings: Optional[dict] = None):
    doc_ref = _db().collection('ai_copilot_documents').document(document_id)
    transaction = _db().transaction()
    return _update_document_txn(transaction, doc_ref, user_id, expected_version, new_layout, new_blocks, record, page_settings)

def undo_document(document_id: str, user_id: str, expected_version: int, new_layout: dict, new_blocks: list, redo_record: dict):
    doc_ref = _db().collection('ai_copilot_documents').document(document_id)
    transaction = _db().transaction()
    return _update_document_txn(transaction, doc_ref, user_id, expected_version, new_layout, new_blocks, is_undo=True, redo_record=redo_record)

def redo_document(document_id: str, user_id: str, expected_version: int, new_layout: dict, new_blocks: list, history_record: dict):
    doc_ref = _db().collection('ai_copilot_documents').document(document_id)
    transaction = _db().transaction()
    return _update_document_txn(transaction, doc_ref, user_id, expected_version, new_layout, new_blocks, is_redo=True, record=history_record)

def get_history(document_id: str):
    doc_ref = _db().collection('ai_copilot_documents').document(document_id)
    history = []
    history_snap = doc_ref.collection('history').order_by('sequence').stream()
    for h in history_snap:
        history.append(h.to_dict())
    return history

def get_redo_stack(document_id: str):
    doc_ref = _db().collection('ai_copilot_documents').document(document_id)
    redo_stack = []
    redo_snap = doc_ref.collection('redo').order_by('sequence').stream()
    for r in redo_snap:
        redo_stack.append(r.to_dict())
    return redo_stack
