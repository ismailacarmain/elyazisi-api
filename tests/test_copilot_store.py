import unittest
from unittest.mock import patch, MagicMock

# Patch the decorator before importing copilot_store
import firebase_admin.firestore
firebase_admin.firestore.transactional = lambda f: f

import copilot_store
from copilot_store import CopilotStoreError, VersionConflictError

class FakeDocRef:
    def __init__(self, doc_id, data=None):
        self.id = doc_id
        self.data = data or {}
        self.collections = {}

    def get(self, transaction=None):
        snap = MagicMock()
        snap.exists = bool(self.data)
        snap.to_dict.return_value = self.data.copy()
        snap.id = self.id
        return snap

    def update(self, new_data):
        self.data.update(new_data)

    def collection(self, name):
        if name not in self.collections:
            self.collections[name] = FakeCollection()
        return self.collections[name]

class FakeCollection:
    def __init__(self):
        self.docs = {}

    def document(self, doc_id):
        if doc_id not in self.docs:
            self.docs[doc_id] = FakeDocRef(doc_id)
        return self.docs[doc_id]

    def stream(self, transaction=None):
        for doc in self.docs.values():
            if doc.data:
                yield doc.get()
                
    def order_by(self, field, direction=None):
        return self

    def list_documents(self):
        return list(self.docs.values())
        
    def limit(self, val):
        return self

class FakeBatch:
    def set(self, ref, data):
        ref.data = data
    def commit(self):
        pass

class FakeTransaction:
    def set(self, ref, data):
        ref.data = data
    def update(self, ref, data):
        ref.data.update(data)
    def delete(self, ref):
        ref.data = {}
        if hasattr(ref, 'id'):
            # Very hacky, but works for our simple mock
            pass

class FakeFirestore:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        if name not in self.collections:
            self.collections[name] = FakeCollection()
        return self.collections[name]

    def batch(self):
        return FakeBatch()

    def transaction(self):
        return FakeTransaction()

class TestCopilotStore(unittest.TestCase):
    def setUp(self):
        self.fake_db = FakeFirestore()
        self.patcher = patch('copilot_store._db', return_value=self.fake_db)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_create_and_load(self):
        doc_id = copilot_store.create_document(
            user_id="user1", font_id="fontA", secondary_font_id="", page_settings={}, version=1, 
            layout={"pages": [{"id": "p1"}]}, blocks=[{"text": "hello"}]
        )
        loaded = copilot_store.get_document(doc_id, "user1")
        self.assertEqual(loaded["id"], doc_id)
        self.assertEqual(loaded["user_id"], "user1")
        self.assertEqual(len(loaded["blocks"]), 1)
        self.assertEqual(loaded["layout"]["pages"][0]["id"], "p1")

    def test_access_denied(self):
        doc_id = copilot_store.create_document(
            user_id="user1", font_id="fontA", secondary_font_id="", page_settings={}, version=1, 
            layout={}, blocks=[]
        )
        with self.assertRaises(CopilotStoreError) as ctx:
            copilot_store.get_document(doc_id, "user2")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_version_conflict(self):
        doc_id = copilot_store.create_document(
            user_id="user1", font_id="fontA", secondary_font_id="", page_settings={}, version=1, 
            layout={}, blocks=[]
        )
        new_version = copilot_store.update_document(doc_id, "user1", 1, {"version": 2}, [], {"instruction": "test"})
        self.assertEqual(new_version, 2)
        
        with self.assertRaises(VersionConflictError):
            copilot_store.update_document(doc_id, "user1", 1, {"version": 3}, [], {"instruction": "test2"})

if __name__ == '__main__':
    unittest.main()
