import copy
import unittest
from unittest.mock import patch

import ai_copilot
import ai_document
import app
from tests.test_ai_document import fake_font


class CopilotPageFitIntegrationTests(unittest.TestCase):
    def _state(self, word_count: int, target_pages: int = 1, settings: dict | None = None):
        blocks = [{"type": "paragraph", "text": "abc " * word_count, "page_break_before": False}]
        settings = settings or {"letter_height_mm": 14, "line_spacing_mm": 20}
        layout = ai_document.build_layout(blocks, fake_font(), settings)
        layout, blocks = ai_copilot.ensure_document_ids(layout, blocks)
        doc = {
            "user_id": "user-a",
            "font_id": "font-a",
            "secondary_font_id": "",
            "page_settings": settings,
            "version": 1,
        }
        result = {
            "needs_clarification": False,
            "new_layout": layout,
            "new_blocks": blocks,
            "operations": [],
            "inverse_operations": [],
            "reflow_needed": False,
            "target_page_count": target_pages,
            "page_target_intent": "exact",
            "assistant_message": "",
        }
        return doc, result

    @patch("app._load_font_images", return_value=fake_font())
    @patch("app._font_access_for_user", return_value=(object(), {}))
    def test_copilot_one_page_command_uses_deterministic_fit_and_is_undoable(self, _access, _load):
        doc, result = self._state(400)
        finalized = app._finalize_copilot_result(doc, result)
        self.assertFalse(finalized["needs_clarification"])
        self.assertEqual(1, len(finalized["new_layout"]["pages"]))
        self.assertTrue(finalized["fit_report"]["fits"])
        self.assertTrue(finalized["operations"])
        self.assertTrue(finalized["inverse_operations"])
        self.assertIn("gerçek font ölçüleriyle", finalized["assistant_message"])

    @patch("app._load_font_images", return_value=fake_font())
    @patch("app._font_access_for_user", return_value=(object(), {}))
    def test_copilot_asks_before_breaking_readability(self, _access, _load):
        doc, result = self._state(
            3000,
            settings={"letter_height_mm": 8, "line_spacing_mm": 10},
        )
        finalized = app._finalize_copilot_result(doc, result)
        self.assertTrue(finalized["needs_clarification"])
        self.assertFalse(finalized["fit_report"]["fits"])
        self.assertEqual([], finalized["operations"])
        self.assertIn("akıllıca kısalt", finalized["clarification_options"][0])

    @patch("app._load_font_images", return_value=fake_font())
    @patch("app._font_access_for_user", return_value=(object(), {}))
    def test_english_page_fit_clarification_contains_no_turkish_fallback(self, _access, _load):
        doc, result = self._state(
            3000,
            settings={"letter_height_mm": 8, "line_spacing_mm": 10},
        )
        finalized = app._finalize_copilot_result(doc, result, ui_language="en")
        self.assertTrue(finalized["needs_clarification"])
        self.assertIn("smallest readable size", finalized["clarification_question"])
        self.assertIn("Shorten the text intelligently", finalized["clarification_options"][0])
        self.assertNotIn("sayfa", finalized["clarification_question"].lower())

    @patch("app._load_font_images", return_value=fake_font())
    @patch("app._font_access_for_user", return_value=(object(), {}))
    def test_global_document_settings_survive_reflow_instead_of_stale_first_page(self, _access, _load):
        doc, result = self._state(120, target_pages=1, settings={
            "letter_height_mm": 10,
            "line_spacing_mm": 14,
            "margin_left_mm": 20,
            "line_slope": 0,
        })
        doc["layout"] = copy.deepcopy(result["new_layout"])
        doc["blocks"] = copy.deepcopy(result["new_blocks"])
        operations = [{
            "operation": "update_document_settings",
            "patch": {"margin_left_mm": 8, "line_slope": 13},
        }]
        clean = ai_copilot.validate_and_sanitize_operations(
            operations, result["new_layout"], result["new_blocks"]
        )
        patched_layout, patched_blocks, _ = ai_copilot.apply_operations(
            clean, result["new_layout"], result["new_blocks"]
        )
        rebuilt, _, _ = app._copilot_reflow_state(
            doc, patched_layout, patched_blocks, 2, operations=clean
        )
        self.assertAlmostEqual(
            8.0, ai_document.px_to_mm(rebuilt["settings"]["margin_left"]), places=1
        )
        self.assertEqual(13, rebuilt["settings"]["line_slope"])

    @patch("app._load_font_images", return_value=fake_font())
    @patch("app._font_access_for_user", return_value=(object(), {}))
    def test_copilot_asks_to_expand_content_for_an_underfilled_target(self, _access, _load):
        doc, result = self._state(1, target_pages=2)
        finalized = app._finalize_copilot_result(doc, result)
        self.assertTrue(finalized["needs_clarification"])
        self.assertEqual("underflow", finalized["fit_report"]["constraint"])
        self.assertIn("genişlet", finalized["clarification_options"][0])

    @patch("app._load_font_images", return_value=fake_font())
    @patch("app._font_access_for_user", return_value=(object(), {}))
    def test_saved_page_target_is_reapplied_on_a_later_copilot_edit(self, _access, _load):
        doc, result = self._state(400)
        fitted = app._finalize_copilot_result(doc, result)
        doc["page_settings"] = fitted["page_settings_update"]
        doc["layout"] = fitted["new_layout"]
        doc["blocks"] = fitted["new_blocks"]
        follow_up = {
            "needs_clarification": False,
            "new_layout": doc["layout"],
            "new_blocks": doc["blocks"],
            "operations": [],
            "inverse_operations": [],
            "reflow_needed": True,
            "page_target_intent": "",
            "assistant_message": "",
        }
        finalized = app._finalize_copilot_result(doc, follow_up)
        self.assertFalse(finalized["needs_clarification"])
        self.assertEqual(1, len(finalized["new_layout"]["pages"]))

    @patch("app._load_font_images", return_value=fake_font())
    @patch("app._font_access_for_user", return_value=(object(), {}))
    def test_line_style_survives_reflow_for_active_page_target(self, _access, _load):
        doc, result = self._state(400)
        source_line = result["new_layout"]["pages"][0]["lines"][0]
        operations = [{
            "operation": "update_line_style",
            "target_id": source_line["id"],
            "patch": {"line_slope": 17},
        }]
        clean = ai_copilot.validate_and_sanitize_operations(
            operations, result["new_layout"], result["new_blocks"]
        )
        patched_layout, patched_blocks, inverse = ai_copilot.apply_operations(
            clean, result["new_layout"], result["new_blocks"]
        )
        result.update({
            "new_layout": patched_layout,
            "new_blocks": patched_blocks,
            "operations": clean,
            "inverse_operations": inverse,
        })
        finalized = app._finalize_copilot_result(doc, result)
        self.assertFalse(finalized["needs_clarification"])
        block_id = source_line["block_id"]
        target_lines = [
            line for page in finalized["new_layout"]["pages"] for line in page["lines"]
            if line.get("block_id") == block_id
        ]
        self.assertTrue(target_lines)
        self.assertEqual(17, target_lines[0]["line_slope"])

    @patch("app._load_font_images", return_value=fake_font())
    @patch("app._font_access_for_user", return_value=(object(), {}))
    def test_previous_line_override_survives_later_unrelated_reflow(self, _access, _load):
        blocks = [
            {"type": "paragraph", "text": "first block text", "page_break_before": False},
            {"type": "paragraph", "text": "second block text", "page_break_before": False},
        ]
        settings = {"letter_height_mm": 10, "line_spacing_mm": 14}
        layout = ai_document.build_layout(blocks, fake_font(), settings, fake_font())
        layout, blocks = ai_copilot.ensure_document_ids(layout, blocks)
        source_line = next(
            line for page in layout["pages"] for line in page["lines"]
            if line["block_id"] == blocks[0]["id"]
        )
        original_x = source_line["start_x"]
        original_y = source_line["baseline_y"]
        operations = [
            {
                "operation": "update_line_style",
                "target_id": source_line["id"],
                "patch": {
                    "ink_color": "#123456",
                    "line_slope": 17,
                    "letter_spacing": 8,
                    "word_spacing": 70,
                    "opacity": 0.72,
                    "kalinlik": 2,
                },
            },
            {
                "operation": "move_line",
                "target_id": source_line["id"],
                "delta_x_px": 24,
                "delta_y_px": 18,
            },
            {
                "operation": "switch_line_author",
                "target_id": source_line["id"],
                "patch": {"font_slot": "secondary"},
            },
        ]
        patched_layout, patched_blocks, inverse = ai_copilot.apply_operations(
            operations, layout, blocks
        )
        doc = {
            "user_id": "user-a",
            "font_id": "font-a",
            "secondary_font_id": "font-b",
            "page_settings": settings,
            "version": 1,
            "layout": layout,
            "blocks": blocks,
        }
        first_result = app._finalize_copilot_result(doc, {
            "needs_clarification": False,
            "new_layout": patched_layout,
            "new_blocks": patched_blocks,
            "operations": operations,
            "inverse_operations": inverse,
            "reflow_needed": False,
            "page_target_intent": "",
            "assistant_message": "",
        }, source_layout=layout)

        later_doc = {
            **doc,
            "version": 2,
            "layout": first_result["new_layout"],
            "blocks": first_result["new_blocks"],
        }
        unrelated = [{
            "operation": "replace_block_text",
            "target_id": blocks[1]["id"],
            "new_text": "unrelated replacement text " * 20,
        }]
        later_layout, later_blocks, later_inverse = ai_copilot.apply_operations(
            unrelated, later_doc["layout"], later_doc["blocks"]
        )
        finalized = app._finalize_copilot_result(later_doc, {
            "needs_clarification": False,
            "new_layout": later_layout,
            "new_blocks": later_blocks,
            "operations": unrelated,
            "inverse_operations": later_inverse,
            "reflow_needed": True,
            "page_target_intent": "",
            "assistant_message": "",
        }, source_layout=later_doc["layout"])
        target = next(
            line for page in finalized["new_layout"]["pages"] for line in page["lines"]
            if line["block_id"] == blocks[0]["id"]
        )
        self.assertEqual("#123456", target["ink_color"])
        self.assertEqual(17, target["line_slope"])
        self.assertEqual(8, target["letter_spacing"])
        self.assertEqual(70, target["word_spacing"])
        self.assertEqual(0.72, target["opacity"])
        self.assertEqual(2, target["kalinlik"])
        self.assertEqual("secondary", target["font_slot"])
        self.assertEqual(original_x + 24, target["start_x"])
        self.assertEqual(original_y + 18, target["baseline_y"])

    @patch("app._load_font_images", return_value=fake_font())
    @patch("app._font_access_for_user", return_value=(object(), {}))
    def test_manual_client_line_override_survives_follow_up_reflow(self, _access, _load):
        blocks = [
            {"type": "paragraph", "text": "manual line", "page_break_before": False},
            {"type": "paragraph", "text": "other block", "page_break_before": False},
        ]
        settings = {"letter_height_mm": 10, "line_spacing_mm": 14}
        stored_layout = ai_document.build_layout(blocks, fake_font(), settings, fake_font())
        stored_layout, blocks = ai_copilot.ensure_document_ids(stored_layout, blocks)
        client_layout = copy.deepcopy(stored_layout)
        client_line = client_layout["pages"][0]["lines"][0]
        client_line.update({
            "start_x": client_line["start_x"] + 16,
            "baseline_y": client_line["baseline_y"] + 12,
            "ink_color": "#654321",
            "line_slope": 13,
            "letter_spacing": 6,
            "word_spacing": 66,
            "opacity": 0.68,
            "kalinlik": 3,
            "font_slot": "secondary",
        })
        sanitized = ai_document.validate_layout(client_layout)
        sanitized["settings"] = copy.deepcopy(stored_layout.get("settings", {}))
        app._capture_manual_line_override_metadata(stored_layout, sanitized)
        sanitized, blocks = ai_copilot.ensure_document_ids(sanitized, blocks)

        doc = {
            "user_id": "user-a",
            "font_id": "font-a",
            "secondary_font_id": "font-b",
            "page_settings": settings,
            "version": 2,
            "layout": sanitized,
            "blocks": blocks,
        }
        changed_blocks = copy.deepcopy(blocks)
        changed_blocks[1]["text"] = "other block changed " * 20
        rebuilt, _, _ = app._copilot_reflow_state(
            doc, sanitized, changed_blocks, 3, operations=[]
        )
        target = next(
            line for page in rebuilt["pages"] for line in page["lines"]
            if line["block_id"] == blocks[0]["id"]
        )
        for field, expected in {
            "start_x": client_line["start_x"],
            "baseline_y": client_line["baseline_y"],
            "ink_color": "#654321",
            "line_slope": 13,
            "letter_spacing": 6,
            "word_spacing": 66,
            "opacity": 0.68,
            "kalinlik": 3,
            "font_slot": "secondary",
        }.items():
            self.assertEqual(expected, target[field], field)

    def test_manual_target_intent_clears_a_saved_target(self):
        doc, result = self._state(20)
        doc["page_settings"]["target_page_count"] = 1
        result["page_target_intent"] = "manual"
        finalized = app._finalize_copilot_result(doc, result)
        self.assertNotIn("target_page_count", finalized["page_settings_update"])

    def test_partial_client_settings_keep_a_saved_page_target(self):
        settings = app._effective_page_settings(
            {},
            {"letter_height_mm": 12},
            {"letter_height_mm": 11, "target_page_count": 1},
        )
        self.assertEqual(1, settings["target_page_count"])
        self.assertEqual(12, settings["letter_height_mm"])

    def test_server_manual_snapshot_can_clear_a_saved_page_target(self):
        settings = app._effective_page_settings(
            {"page_settings_update": {"letter_height_mm": 12}},
            None,
            {"letter_height_mm": 11, "target_page_count": 1},
        )
        self.assertNotIn("target_page_count", settings)
        self.assertEqual(12, settings["letter_height_mm"])

    def test_effective_settings_use_copilot_layout_values_over_stale_client_values(self):
        settings = app._effective_page_settings(
            {
                "new_layout": {
                    "settings": {
                        "line_slope": 11,
                        "units": {
                            "letter_height_mm": 6.25,
                            "line_spacing_mm": 7.4,
                        },
                    },
                },
            },
            {"line_slope": 3, "letter_height_mm": 12},
            {"line_slope": 1, "letter_height_mm": 11, "target_page_count": 1},
        )
        self.assertEqual(11, settings["line_slope"])
        self.assertEqual(6.25, settings["letter_height_mm"])
        self.assertEqual(7.4, settings["line_spacing_mm"])
        self.assertEqual(1, settings["target_page_count"])

    def test_manual_state_preflight_allows_cross_origin_patch(self):
        response = app.app.test_client().open(
            "/api/ai/documents/example/state",
            method="OPTIONS",
            headers={
                "Origin": "https://fontify.online",
                "Access-Control-Request-Method": "PATCH",
            },
        )
        self.assertEqual(204, response.status_code)
        self.assertIn("PATCH", response.headers.get("Access-Control-Allow-Methods", ""))

    def test_loopback_http_health_is_not_forced_to_unsupported_tls(self):
        with patch.dict(app.app.config, {"DEBUG": False}):
            response = app.app.test_client().get(
                "/health",
                base_url="http://127.0.0.1:5055",
                environ_base={"REMOTE_ADDR": "127.0.0.1"},
            )
        self.assertEqual(200, response.status_code)

    def test_non_loopback_http_still_redirects_to_https(self):
        with patch.dict(app.app.config, {"DEBUG": False}):
            response = app.app.test_client().get(
                "/health",
                base_url="http://example.test",
                environ_base={"REMOTE_ADDR": "203.0.113.10"},
            )
        self.assertEqual(301, response.status_code)
        self.assertTrue(response.headers["Location"].startswith("https://"))

    def test_readiness_reports_firebase_without_exposing_secrets(self):
        with patch.object(app, "db", object()), \
             patch.object(app, "connected_project_id", "elyazisiapp"), \
             patch.dict("os.environ", {"FIREBASE_PROJECT_ID": "elyazisiapp"}, clear=False):
            response = app.app.test_client().get("/health/ready")
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["firebase_ready"])
        self.assertTrue(payload["firebase_project_ready"])
        self.assertNotIn("credential", str(payload).lower())
        self.assertNotIn("key", str(payload).lower())

    @patch("app.auth.verify_id_token", side_effect=ValueError("invalid token"))
    def test_invalid_token_error_is_valid_turkish_utf8(self, _verify):
        response = app.app.test_client().get(
            "/api/ai/status",
            headers={"Authorization": "Bearer invalid-token"},
        )
        self.assertEqual(401, response.status_code)
        payload = response.get_json()
        self.assertEqual(
            "Oturum doğrulanamadı. Lütfen yeniden giriş yapın.",
            payload["message"],
        )
        self.assertNotIn("Ã", payload["message"])
        self.assertNotIn("Ä", payload["message"])
        self.assertNotIn("Å", payload["message"])

    @patch("app.auth.verify_id_token", return_value={"uid": "user-a"})
    def test_invalid_document_version_returns_controlled_400(self, _verify):
        with patch("app._get_copilot_doc", return_value={"version": 3}):
            response = app.app.test_client().post(
                "/api/ai/documents/document-1/edits",
                headers={"Authorization": "Bearer test-token"},
                json={"instruction": "Başlığı düzelt", "document_version": "abc"},
            )
        self.assertEqual(400, response.status_code)
        self.assertEqual("Belge sürümü geçersiz.", response.get_json()["message"])

    @patch("app.auth.verify_id_token", return_value={"uid": "user-a"})
    def test_redo_uses_stored_secondary_font_even_if_multi_author_started_off(self, _verify):
        blocks = [{"type": "paragraph", "text": "abc", "page_break_before": False}]
        layout = ai_document.build_layout(blocks, fake_font(), {"multi_author": False})
        layout, blocks = ai_copilot.ensure_document_ids(layout, blocks)
        block_id = blocks[0]["id"]
        record = {
            "instruction": "İkinci yazarla yaz",
            "operations": [{
                "operation": "switch_block_author",
                "target_id": block_id,
                "patch": {"author_slot": "secondary"},
            }],
            "page_settings_after": {},
        }
        doc = {
            "version": 2,
            "layout": layout,
            "blocks": blocks,
            "history": [],
            "redo_stack": [record],
            "secondary_font_id": "font-b",
        }
        with patch("app._get_copilot_doc", return_value=doc), \
             patch("app._copilot_reflow_state", side_effect=lambda _doc, new_layout, new_blocks, *_args: (new_layout, new_blocks, {})), \
             patch("app._store.redo_document") as redo_document:
            response = app.app.test_client().post(
                "/api/ai/documents/document-1/redo",
                headers={"Authorization": "Bearer test-token"},
            )
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual("secondary", payload["new_blocks"][0]["author_slot"])
        redo_document.assert_called_once()

    @patch("app._store.create_document", return_value="document-1")
    @patch("app.auth.verify_id_token", return_value={"uid": "user-a"})
    def test_initial_copilot_document_persists_flat_validated_settings(self, _auth, create):
        blocks = [{"type": "paragraph", "text": "Deneme metni.", "page_break_before": False}]
        layout = ai_document.build_layout(blocks, fake_font(), {"letter_height_mm": 12})
        response = app.app.test_client().post(
            "/api/ai/documents",
            headers={"Authorization": "Bearer test-token"},
            json={
                "layout": layout,
                "blocks": blocks,
                "page_settings": {"letter_height_mm": 12, "target_page_count": 1},
            },
        )
        self.assertEqual(200, response.status_code)
        persisted = create.call_args.kwargs["page_settings"]
        self.assertEqual(12, persisted["letter_height_mm"])
        self.assertEqual(1, persisted["target_page_count"])
        self.assertNotIn("units", persisted)

    @patch("app.auth.verify_id_token", return_value={"uid": "user-a"})
    def test_manual_reflow_preserves_block_semantics_and_exact_page_target(self, _auth):
        blocks = [
            {
                "id": "title-1",
                "type": "title",
                "text": "Korunan başlık",
                "color": "#123456",
                "align": "center",
                "scale_multiplier": 1.2,
                "page_break_before": False,
            },
            {
                "id": "paragraph-1",
                "type": "paragraph",
                "text": "abc " * 400,
                "page_break_before": False,
            },
        ]
        with patch("app._font_access_for_user", return_value=(object(), {"repetition": 1})), \
             patch("app._load_font_images", return_value=fake_font()), \
             patch("app._load_secondary_font", return_value=(None, None)):
            response = app.app.test_client().post(
                "/api/ai/plan",
                headers={"Authorization": "Bearer test-token"},
                json={
                    "source": "manual",
                    "font_id": "font-a",
                    "text_content": "edited document",
                    "blocks": blocks,
                    "page_settings": {
                        "letter_height_mm": 14,
                        "line_spacing_mm": 20,
                        "target_page_count": 1,
                    },
                },
            )
        self.assertEqual(200, response.status_code, response.get_json())
        payload = response.get_json()
        self.assertFalse(payload["needs_clarification"])
        self.assertEqual(1, len(payload["layout"]["pages"]))
        self.assertTrue(payload["fit_report"]["fits"])
        self.assertEqual(1, payload["updated_settings"]["target_page_count"])
        self.assertEqual("title", payload["blocks"][0]["type"])
        self.assertEqual("#123456", payload["blocks"][0]["color"])
        self.assertEqual("center", payload["blocks"][0]["align"])
        self.assertEqual("title-1", payload["blocks"][0]["id"])

    def test_client_margin_note_target_survives_block_sanitization(self):
        blocks = app._sanitize_client_copilot_blocks([{
            "id": "note-1",
            "type": "paragraph",
            "text": "Kısa kenar notu",
            "is_margin_note": True,
            "target_page_id": "page-2",
            "page_break_before": False,
        }])
        self.assertEqual("note-1", blocks[0]["id"])
        self.assertEqual("page-2", blocks[0]["target_page_id"])


if __name__ == "__main__":
    unittest.main()
