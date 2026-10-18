import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import shutil
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("persona_review", ROOT / "scripts" / "persona_review.py")
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)


def review(status="complete", severity=None):
    return {"version": 1, "scope": {"mode": "diff", "reason": "test fixture", "reviewed": ["toy.py"],
            "unverified": [] if status == "complete" else ["tests not run"], "status": status},
            "summary": "fixture", "strengths": [], "findings": [] if not severity else [{
                "id": "F1", "severity": severity, "title": "fixture", "file": "toy.py", "line": 1,
                "evidence": "private-code-canary", "impact": "fixture", "suggestion": "fixture"}]}


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        # Windows sandbox identities cannot enter Python 3.13 mode-0700 temp dirs.
        # Ordinary mkdir inherits the host's temp-directory ACL.
        self.root = Path(tempfile.gettempdir()) / ("persona-review-test-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.data = self.root / "data"

    def save(self, doc):
        pr.validate_config(doc)
        pr.atomic_json(self.data / "config.json", doc)

    def test_resolution_precedence_and_project_policy(self):
        project = self.root / "project.json"
        pr.atomic_json(project, {"image_mode": "B", "review_mode": "diff"})
        doc = pr.initial()
        doc["settings"]["image_mode"] = "A"
        self.save(doc)
        resolved = pr.resolve(self.data, project)
        self.assertEqual(resolved["settings"]["image_mode"], "always")
        self.assertTrue(resolved["project_ignored"])
        doc["allow_project_overrides"] = True
        self.save(doc)
        resolved = pr.resolve(self.data, project, {"image_mode": "hybrid"})
        self.assertEqual(resolved["settings"], {"character": "guide", "character_id": "guide", "persona_id": "guide",
                         "image_mode": "hybrid", "review_mode": "diff", "scope": "diff", "focus": []})
        self.assertEqual(resolved["sources"], {"character": "bundled", "character_id": "bundled", "persona_id": "character_default",
                         "image_mode": "explicit", "review_mode": "project", "scope": "project", "focus": "bundled"})

    def test_disabled_project_is_not_read(self):
        self.assertTrue(pr.resolve(self.data, self.root / "does-not-exist")["project_ignored"])

    def test_project_update_preserves_other_settings_and_rejects_unknown(self):
        project = self.root / ".persona-review.json"
        pr.atomic_json(project, {"review_mode": "diff"})
        self.assertEqual(pr.main(["--data-dir", str(self.data), "set-project", str(project), "image_mode", "B"]), 0)
        self.assertEqual(pr.read_json(project), {"review_mode": "diff", "image_mode": "pre_generate"})
        before = project.read_bytes()
        self.assertEqual(pr.main(["--data-dir", str(self.data), "set-project", str(project), "character", "unknown"]), 2)
        self.assertEqual(project.read_bytes(), before)

    def test_project_rejects_paths_commands_and_policy(self):
        doc = pr.initial()
        doc["allow_project_overrides"] = True
        self.save(doc)
        for dangerous in ({"reference": "../../secret"}, {"command": "run me"}, {"allow_project_overrides": True}):
            project = self.root / "project.json"
            pr.atomic_json(project, dangerous)
            with self.assertRaises(pr.Invalid):
                pr.resolve(self.data, project)

    def test_invalid_update_preserves_config(self):
        self.save(pr.initial())
        before = (self.data / "config.json").read_bytes()
        with self.assertRaises(pr.Invalid):
            pr.update(self.data, lambda doc: doc["settings"].update(image_mode="evil"))
        self.assertEqual((self.data / "config.json").read_bytes(), before)
        self.assertFalse((self.data / "config.lock").exists())

    def test_json_byte_limit_boundary(self):
        path = self.root / "boundary.json"
        # Include multibyte text and the trailing newline in the byte limit.
        text_bytes = pr.MAX_JSON_BYTES - len(b'{\n  "text": ""\n}\n')
        count, remainder = divmod(text_bytes, len("あ".encode("utf-8")))
        doc = {"text": "あ" * count + "x" * remainder}
        pr.atomic_json(path, doc)
        before = path.read_bytes()
        self.assertEqual(len(before), pr.MAX_JSON_BYTES)
        self.assertEqual(pr.read_json(path), doc)

        with self.assertRaisesRegex(pr.Invalid, "JSON exceeds"):
            pr.atomic_json(path, {"text": doc["text"] + "x"})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".pending-*")), [])

        oversized = self.root / "oversized.json"
        oversized.write_bytes(before + b" ")
        with self.assertRaisesRegex(pr.Invalid, "JSON exceeds"):
            pr.read_json(oversized)

    def test_oversized_registration_preserves_readable_config(self):
        character = {"name": "n", "persona": "x" * 2000,
                     "appearance": "y" * 2000, "images": {}}
        doc = pr.initial()
        doc["characters"] = {f"char{i}": copy.deepcopy(character) for i in range(486)}
        self.save(doc)
        path = self.data / "config.json"
        before = path.read_bytes()
        self.assertLessEqual(len(before), pr.MAX_JSON_BYTES)
        definition = self.root / "character.json"
        pr.atomic_json(definition, character)

        self.assertEqual(pr.main(["--data-dir", str(self.data), "register",
                                  "char486", str(definition)]), 2)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(pr.load_config(self.data), doc)
        self.assertFalse((self.data / "config.lock").exists())
        self.assertEqual(list(self.data.glob(".pending-*")), [])

    def test_replace_failure_preserves_config_and_cleans_temp(self):
        self.save(pr.initial())
        before = (self.data / "config.json").read_bytes()
        with patch.object(pr.os, "replace", side_effect=OSError("simulated disk failure")):
            with self.assertRaises(OSError):
                pr.update(self.data, lambda doc: doc["settings"].update(image_mode="B"))
        self.assertEqual((self.data / "config.json").read_bytes(), before)
        self.assertEqual(list(self.data.glob(".pending-*")), [])
        self.assertFalse((self.data / "config.lock").exists())

    def test_lock_rejects_concurrent_update(self):
        with pr.locked(self.data):
            with self.assertRaises(pr.Invalid):
                pr.update(self.data, lambda doc: None)

    def test_unknown_character_and_bool_version_rejected(self):
        with self.assertRaises(pr.Invalid):
            pr.resolve(self.data, explicit={"character": "not-registered"})
        doc = pr.initial()
        doc["version"] = True
        with self.assertRaises(pr.Invalid):
            pr.validate_config(doc)

    def test_duplicate_json_keys_rejected(self):
        path = self.root / "duplicate.json"
        path.write_text('{"image_mode":"B","image_mode":"A"}')
        with self.assertRaises(pr.Invalid):
            pr.read_json(path)

    def test_image_mode_routing_and_no_review_text_leak(self):
        # Header fixture tests routing only, not decoding; skill requires visual inspection.
        source = self.root / "fixture.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        reference = pr.import_image(self.data, "guide", "reference", source)
        registered = pr.import_image(self.data, "guide", "stern", source)
        for mode, expected in (("A", "generate"), ("always", "generate"), ("B", "prepare_set"), ("hybrid", "use_cached")):
            resolved = pr.resolve(self.data, explicit={"image_mode": mode})
            plan = pr.visual_plan(self.data, resolved, review(severity="high"))
            self.assertEqual(plan["action"], expected)
            self.assertNotIn("private-code-canary", json.dumps(plan))
            self.assertNotIn("persona", plan)
        (self.data / "assets" / registered).unlink()
        self.assertEqual(pr.visual_plan(self.data, pr.resolve(self.data), review(severity="high"))["action"], "generate")
        (self.data / "assets" / reference).unlink()
        self.assertEqual(pr.visual_plan(self.data, pr.resolve(self.data), review(severity="high"))["action"], "unavailable")

    def test_legacy_b_maps_to_finite_set_without_reference(self):
        resolved = pr.resolve(self.data, explicit={"image_mode": "B"})
        self.assertEqual(resolved["settings"]["image_mode"], "pre_generate")
        plan = pr.visual_plan(self.data, resolved, review())
        self.assertEqual(plan["expressions"], list(pr.EXPRESSIONS))
        self.assertTrue(all(item["action"] == "unavailable" for item in plan["plans"]))

    def test_import_rejects_executable_and_traversal(self):
        source = self.root / "fake.png"
        source.write_bytes(b"MZ fake executable")
        with self.assertRaises(pr.Invalid):
            pr.import_image(self.data, "guide", "reference", source)
        for bad in ("../secret.png", "C:/secret.png", "a.png"):
            with self.assertRaises(pr.Invalid):
                pr.asset_path(self.data, bad)

    def test_symlink_asset_escape(self):
        self.data.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        try:
            (self.data / "assets").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation not available on this host")
        with self.assertRaises(pr.Invalid):
            pr.asset_path(self.data, "a" * 32 + ".png")

    def test_character_update_keeps_images(self):
        source = self.root / "fixture.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        asset = pr.import_image(self.data, "guide", "reference", source)
        definition = self.root / "character.json"
        pr.atomic_json(definition, pr.GUIDE | {"name": "new name"})
        self.assertEqual(pr.main(["--data-dir", str(self.data), "register", "guide", str(definition)]), 0)
        self.assertEqual(pr.load_config(self.data)["characters"]["guide"]["images"]["reference"], asset)

    def image(self, name="source.png", payload=b"fixture"):
        path = self.root / name
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + payload)
        return path

    def plan(self, mode="hybrid", mood="neutral"):
        resolved = pr.resolve(self.data, explicit={"image_mode": mode})
        pr.migrate_catalog(self.data)
        return pr.expression_plan(self.data, resolved, mood, force=mode == "always")

    def test_generated_history_and_idempotent_save_in_always_and_hybrid(self):
        source = self.image()
        pr.import_image(self.data, "guide", "reference", source)
        first_plan = self.plan()
        first = pr.save_generated(self.data, first_plan, source)
        self.assertEqual(self.plan()["record_id"], first["id"])
        self.assertEqual(first["origin"], "image_gen")
        self.assertEqual(first["expression"], "neutral")
        self.assertEqual(first["reference_sha256"], pr.digest(source.read_bytes()))
        self.assertEqual(pr.save_generated(self.data, first_plan, source), first)
        second_plan = self.plan("always")
        self.assertEqual(second_plan["action"], "generate")
        second = pr.save_generated(self.data, second_plan, self.image("new.png", b"new-image"))
        self.assertNotEqual(first["asset"], second["asset"])
        self.assertEqual(self.plan()["record_id"], second["id"])
        self.assertTrue(pr.asset_path(self.data, first["asset"]).is_file())
        self.assertEqual(len(list((self.data / "image-records").glob("*.json"))), 2)
        with self.assertRaisesRegex(pr.Invalid, "different result"):
            pr.save_generated(self.data, first_plan, self.image("different.png", b"different"))

    def test_pre_generate_reuses_completed_items_and_retries_only_missing(self):
        source = self.image()
        pr.import_image(self.data, "guide", "reference", source)
        pr.import_image(self.data, "guide", "stern", source)
        resolved = pr.resolve(self.data, explicit={"image_mode": "pre_generate"})
        planned = pr.visual_plan(self.data, resolved, review(severity="high"))
        self.assertEqual(planned["action"], "prepare_set")
        self.assertEqual(planned["expression"], "stern")
        self.assertEqual([p["expression"] for p in planned["plans"]], list(pr.EXPRESSIONS))
        self.assertEqual(planned["plans"][-1]["action"], "use_cached")
        for plan in planned["plans"][:2]:
            pr.save_generated(self.data, plan, source)
        # The concerned call failed before a result existed; no success record is fabricated.
        retry = pr.set_plan(self.data, resolved)
        self.assertEqual([p["expression"] for p in retry["plans"] if p["action"] == "generate"], ["concerned"])
        pr.save_generated(self.data, retry["plans"][2], source)
        self.assertTrue(pr.set_plan(self.data, resolved)["complete"])
        self.assertEqual(pr.visual_plan(self.data, resolved, review(severity="high"))["action"], "use_cached")

    def test_character_identity_and_reference_changes_isolate_cache(self):
        source = self.image()
        pr.import_image(self.data, "guide", "reference", source)
        saved = pr.save_generated(self.data, self.plan(), source)
        pr.register_persona(self.data, "guide", {"name": "guide", "style": "new voice"})
        self.assertEqual(self.plan()["record_id"], saved["id"])
        pr.update(self.data, lambda d: d["characters"]["guide"].update(appearance="different person"))
        self.assertEqual(self.plan()["action"], "generate")
        pr.update(self.data, lambda d: d["characters"]["guide"].update(appearance=""))
        self.assertEqual(self.plan()["record_id"], saved["id"])
        pr.update(self.data, lambda d: d["characters"]["guide"].update(name="different name"))
        self.assertEqual(self.plan()["action"], "generate")
        pr.update(self.data, lambda d: d["characters"]["guide"].update(name=pr.GUIDE["name"]))
        pr.import_image(self.data, "guide", "reference", self.image("ref2.png", b"ref2"))
        self.assertEqual(self.plan()["action"], "generate")
        pr.import_image(self.data, "guide", "reference", source)
        self.assertEqual(self.plan()["record_id"], saved["id"])
        pr.update(self.data, lambda d: d["characters"].update(other=copy.deepcopy(d["characters"]["guide"])))
        other = pr.resolve(self.data, explicit={"character": "other"})
        self.assertEqual(pr.expression_plan(self.data, other, "neutral")["action"], "generate")

    def test_generation_finishing_after_reference_change_retains_original_identity(self):
        source = self.image()
        pr.import_image(self.data, "guide", "reference", source)
        original_plan = self.plan()
        pr.import_image(self.data, "guide", "reference", self.image("changed.png", b"changed"))
        result = pr.save_generated(self.data, original_plan, source)
        self.assertEqual(result["context_key"], original_plan["context_key"])
        self.assertEqual(self.plan()["action"], "generate")
        pr.import_image(self.data, "guide", "reference", source)
        self.assertEqual(self.plan()["record_id"], result["id"])

    def test_legacy_migration_is_idempotent_and_precedes_reference_change(self):
        source = self.image()
        old_asset = "a" * 32 + ".png"
        old_reference = "b" * 32 + ".png"
        (self.data / "assets").mkdir(parents=True)
        for asset in (old_asset, old_reference):
            (self.data / "assets" / asset).write_bytes(source.read_bytes())
        doc = pr.initial()
        doc["settings"]["image_mode"] = "B"
        doc["characters"]["guide"] = copy.deepcopy(pr.GUIDE)
        doc["characters"]["guide"]["images"] = {"reference": old_reference, "neutral": old_asset}
        self.save(doc)
        original_config = (self.data / "config.json").read_bytes()
        pr.migrate_catalog(self.data)
        pr.migrate_catalog(self.data)
        self.assertEqual((self.data / "config.json").read_bytes(), original_config)
        self.assertEqual(self.plan()["origin"], "legacy")
        self.assertEqual(len(list((self.data / "image-records").glob("*.json"))), 1)
        pr.import_image(self.data, "guide", "reference", self.image("new-ref.png", b"different"))
        self.assertEqual(self.plan()["action"], "generate")
        self.assertEqual((self.data / "assets" / old_asset).read_bytes(), source.read_bytes())

    def test_corrupt_and_rejected_images_remain_on_disk_but_are_not_reused(self):
        source = self.image()
        pr.import_image(self.data, "guide", "reference", source)
        record = pr.save_generated(self.data, self.plan(), source)
        asset = pr.asset_path(self.data, record["asset"])
        asset.write_bytes(b"\x89PNG\r\n\x1a\nchanged")
        self.assertEqual(self.plan()["action"], "generate")
        asset.write_bytes(source.read_bytes())
        pr.invalidate_image(self.data, record["id"])
        self.assertEqual(self.plan()["action"], "generate")
        self.assertTrue(asset.exists())

    def test_failed_image_save_does_not_destroy_previous_image_and_is_retryable(self):
        source = self.image()
        pr.import_image(self.data, "guide", "reference", source)
        old = pr.save_generated(self.data, self.plan(), source)
        plan = self.plan("always")
        with patch.object(pr.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                pr.save_generated(self.data, plan, source)
        self.assertEqual(self.plan()["record_id"], old["id"])
        self.assertFalse((self.data / "config.lock").exists())
        self.assertFalse(pr.record_path(self.data, plan["request_id"]).exists())
        saved = pr.save_generated(self.data, plan, source)
        self.assertEqual(self.plan()["record_id"], saved["id"])
        self.assertTrue(pr.asset_path(self.data, old["asset"]).exists())

    def test_expression_import_record_failure_preserves_authoritative_binding(self):
        source = self.image()
        pr.import_image(self.data, "guide", "reference", source)
        old_asset = pr.import_image(self.data, "guide", "neutral", source)
        old_plan = self.plan()
        config_before = (self.data / "config.json").read_bytes()
        newer = self.image("newer.png", b"new-expression")
        with patch.object(pr, "save_record", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                pr.import_image(self.data, "guide", "neutral", newer)
        self.assertEqual((self.data / "config.json").read_bytes(), config_before)
        self.assertEqual(self.plan()["record_id"], old_plan["record_id"])
        self.assertEqual(pr.asset_path(self.data, old_asset).read_bytes(), source.read_bytes())
        self.assertFalse((self.data / "config.lock").exists())
        new_asset = pr.import_image(self.data, "guide", "neutral", newer)
        self.assertEqual((self.data / "config.json").read_bytes(), config_before)
        self.assertEqual(Path(self.plan()["image_path"]), pr.asset_path(self.data, new_asset))
        self.assertTrue(pr.asset_path(self.data, old_asset).exists())

    def test_missing_references_cannot_share_legacy_expression_cache(self):
        source = self.image()
        old_asset = "a" * 32 + ".png"
        missing_reference = "b" * 32 + ".png"
        (self.data / "assets").mkdir(parents=True)
        (self.data / "assets" / old_asset).write_bytes(source.read_bytes())
        doc = pr.initial()
        doc["characters"]["guide"] = copy.deepcopy(pr.GUIDE)
        doc["characters"]["guide"]["images"] = {"reference": missing_reference, "neutral": old_asset}
        self.save(doc)
        pr.migrate_catalog(self.data)
        self.assertEqual(self.plan()["action"], "unavailable")
        new_reference = pr.import_image(self.data, "guide", "reference", self.image("new-ref.png", b"different"))
        pr.asset_path(self.data, new_reference).unlink()
        self.assertEqual(self.plan()["action"], "unavailable")
        self.assertEqual((self.data / "assets" / old_asset).read_bytes(), source.read_bytes())
        self.assertEqual(len(list((self.data / "image-records").glob("*.json"))), 1)

    def test_forged_generation_context_and_record_path_rejected(self):
        source = self.image()
        pr.import_image(self.data, "guide", "reference", source)
        plan = self.plan()
        plan["context_key"] = "0" * 64
        with self.assertRaises(pr.Invalid):
            pr.save_generated(self.data, plan, source)
        with self.assertRaises(pr.Invalid):
            pr.record_path(self.data, "../escape")

    def test_invalid_result_does_not_complete_missing_expression(self):
        source = self.image()
        pr.import_image(self.data, "guide", "reference", source)
        bad = self.root / "bad.png"
        bad.write_bytes(b"not an image")
        with self.assertRaises(pr.Invalid):
            pr.save_generated(self.data, self.plan(), bad)
        self.assertEqual(self.plan()["action"], "generate")


class ReviewTests(unittest.TestCase):
    def test_severity_precedes_missing_coverage(self):
        self.assertEqual(pr.expression(review("partial", "critical")), "stern")
        self.assertEqual(pr.expression(review("partial", "low")), "concerned")
        self.assertEqual(pr.expression(review("partial")), "neutral")
        self.assertEqual(pr.expression(review()), "happy")

    def test_empty_sample_is_neutral(self):
        self.assertEqual(pr.expression(pr.read_json(ROOT / "examples" / "review.json")), "neutral")

    def test_false_complete_and_false_empty_rejected(self):
        for change in ({"reviewed": []}, {"unverified": ["not tested"]}):
            doc = review()
            doc["scope"].update(change)
            with self.assertRaises(pr.Invalid):
                pr.validate_review(doc)
        with self.assertRaises(pr.Invalid):
            pr.validate_review(review("empty", "high"))

    def test_bad_finding_rejected(self):
        for field, value in (("severity", "catastrophic"), ("line", True), ("line", 0), ("evidence", "")):
            doc = review(severity="high")
            doc["findings"][0][field] = value
            with self.assertRaises(pr.Invalid):
                pr.validate_review(doc)
        doc = review(severity="high")
        doc["findings"].append(copy.deepcopy(doc["findings"][0]))
        with self.assertRaises(pr.Invalid):
            pr.validate_review(doc)


if __name__ == "__main__":
    unittest.main()
