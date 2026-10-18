"""Offline behavior checks for migration, independent settings, and image policy."""
import copy
import json
import unittest
from unittest.mock import patch

import test_persona_review as helpers

pr = helpers.pr


class ConfigurationV2Tests(unittest.TestCase):
    setUp = helpers.ConfigurationTests.setUp
    save = helpers.ConfigurationTests.save
    image = helpers.ConfigurationTests.image

    def register(self, key="mina", persona="calm"):
        pr.register_persona(self.data, persona, {"name": persona, "style": "calm and precise"})
        pr.register_character(self.data, key, {"name": key, "appearance": "blue coat",
                                               "persona_id": persona, "images": {}})

    def test_legacy_read_is_nonmutating_and_preserves_each_voice(self):
        doc = pr.initial()
        for key in ("mina", "other"):
            doc["characters"][key] = pr.GUIDE | {"persona": key + " voice"}
        doc["settings"] = {"character": "mina", "review_mode": "full", "image_mode": "B"}
        self.save(doc)
        before = (self.data / "config.json").read_bytes()
        for key in ("mina", "other"):
            result = pr.resolve(self.data, explicit={"character_id": key})
            self.assertEqual(result["persona"]["style"], key + " voice")
            self.assertEqual(result["settings"]["scope"], "full")
            self.assertEqual(result["settings"]["image_mode"], "pre_generate")
        self.assertEqual((self.data / "config.json").read_bytes(), before)
        self.assertEqual(list(self.data.glob("config.v1-backup-*")), [])

    def test_migration_preserves_exact_backup_images_history_and_long_ids(self):
        key = "c" * 48
        doc = pr.initial()
        doc["characters"][key] = copy.deepcopy(pr.GUIDE)
        doc["settings"] = {"character": key, "review_mode": "diff", "image_mode": "A"}
        asset = "a" * 32 + ".png"
        (self.data / "assets").mkdir(parents=True)
        (self.data / "assets" / asset).write_bytes(self.image().read_bytes())
        doc["characters"][key]["images"]["neutral"] = asset
        self.save(doc)
        # Keep unusual whitespace/BOM too, rather than reserializing the backup.
        path = self.data / "config.json"
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes() + b"  \n")
        before = path.read_bytes()
        legacy = pr.resolve(self.data, explicit={"image_mode": "cached_only"})
        pr.migrate_catalog(self.data)
        old_plan = pr.expression_plan(self.data, legacy, "neutral")
        self.assertEqual(pr.main(["--data-dir", str(self.data), "migrate"]), 0)
        migrated = pr.load_config(self.data)
        self.assertEqual(migrated["version"], 2)
        self.assertEqual(migrated["settings"], {"character_id": key, "scope": "diff", "image_mode": "always"})
        self.assertEqual(migrated["characters"][key]["images"], {"neutral": asset})
        self.assertEqual(len(migrated["characters"][key]["persona_id"]), 31)
        backups = list(self.data.glob("config.v1-backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), before)
        pr.update(self.data, lambda doc: None)
        self.assertEqual(list(self.data.glob("config.v1-backup-*")), backups)
        new_plan = pr.expression_plan(self.data, pr.resolve(self.data, explicit={"image_mode": "cached_only"}), "neutral")
        self.assertEqual(new_plan["record_id"], old_plan["record_id"])
        self.assertTrue((self.data / "assets" / asset).exists())

    def test_backup_failure_preserves_v1_and_is_retryable(self):
        self.save(pr.initial())
        pr.migrate_catalog(self.data)
        before = (self.data / "config.json").read_bytes()
        original_replace = pr.os.replace
        def fail_backup(source, destination):
            if "v1-backup" in str(destination):
                raise OSError("backup failure")
            return original_replace(source, destination)
        with patch.object(pr.os, "replace", side_effect=fail_backup):
            with self.assertRaises(OSError):
                pr.update(self.data, lambda doc: None)
        self.assertEqual((self.data / "config.json").read_bytes(), before)
        self.assertEqual(list(self.data.glob(".pending-*")), [])
        self.assertEqual(list(self.data.glob("config.v1-backup-*")), [])
        pr.update(self.data, lambda doc: None)
        self.assertEqual(pr.load_config(self.data)["version"], 2)

    def test_config_replace_failure_keeps_backup_and_allows_retry(self):
        self.save(pr.initial())
        before = (self.data / "config.json").read_bytes()
        original_replace = pr.os.replace
        def fail_config(source, destination):
            if destination == self.data / "config.json":
                raise OSError("config failure")
            return original_replace(source, destination)
        with patch.object(pr.os, "replace", side_effect=fail_config):
            with self.assertRaises(OSError):
                pr.register_persona(self.data, "calm", {"name": "calm", "style": "precise"})
        self.assertEqual((self.data / "config.json").read_bytes(), before)
        self.assertEqual(next(self.data.glob("config.v1-backup-*")).read_bytes(), before)
        self.assertFalse((self.data / "config.lock").exists())
        pr.register_persona(self.data, "calm", {"name": "calm", "style": "precise"})
        self.assertIn("calm", pr.load_config(self.data)["personas"])

    def test_conflicting_backup_blocks_overwrite(self):
        self.save(pr.initial())
        before = (self.data / "config.json").read_bytes()
        (self.data / ("config.v1-backup-" + pr.digest(before) + ".json")).write_bytes(b"different")
        with self.assertRaisesRegex(pr.Invalid, "backup"):
            pr.update(self.data, lambda doc: None)
        self.assertEqual((self.data / "config.json").read_bytes(), before)

    def test_scope_focus_are_independent_and_legacy_precedence_is_per_layer(self):
        doc = pr.initial()
        doc["allow_project_overrides"] = True
        doc["settings"] = {"scope": "full", "focus": ["correctness"]}
        self.save(doc)
        project = self.root / "project.json"
        pr.atomic_json(project, {"review_mode": "diff", "focus": ["security"]})
        result = pr.resolve(self.data, project, {"scope": "file"})
        self.assertEqual(result["settings"]["scope"], "file")
        self.assertEqual(result["settings"]["focus"], ["security"])
        result = pr.resolve(self.data, project, {"review_mode": "auto", "focus": []})
        self.assertEqual(result["settings"]["scope"], "auto")
        self.assertEqual(result["settings"]["focus"], [])
        self.assertEqual(result["sources"]["focus"], "explicit")

    def test_ambiguous_aliases_rejected_and_equivalent_aliases_accepted(self):
        for values in ({"scope": "file", "review_mode": "diff"}, {"character": "guide", "character_id": "mina"}):
            with self.subTest(values=values), self.assertRaisesRegex(pr.Invalid, "conflicting"):
                pr.resolve(self.data, explicit=values)
        self.assertEqual(pr.resolve(self.data, explicit={"scope": "diff", "review_mode": "diff"})["settings"]["scope"], "diff")

    def test_focus_rejects_duplicates_unknown_values_and_commands(self):
        for focus in (["security", "security"], ["execute tests"], "security", {"command": "run"}):
            with self.subTest(focus=focus), self.assertRaises(pr.Invalid):
                pr.resolve(self.data, explicit={"focus": focus})

    def test_disabled_project_cannot_change_new_settings(self):
        project = self.root / "project.json"
        project.write_text('not JSON', encoding="utf-8")
        result = pr.resolve(self.data, project, {"scope": "file", "focus": ["security"]})
        self.assertTrue(result["project_ignored"])
        self.assertEqual(result["settings"]["scope"], "file")

    def test_cli_global_and_project_updates_preserve_unrelated_settings(self):
        self.assertEqual(pr.main(["--data-dir", str(self.data), "set", "focus", "security,performance"]), 0)
        self.assertEqual(pr.main(["--data-dir", str(self.data), "set", "review_mode", "file"]), 0)
        self.assertEqual(pr.load_config(self.data)["settings"], {"scope": "file", "focus": ["security", "performance"]})
        project = self.root / ".persona-review.json"
        pr.atomic_json(project, {"review_mode": "diff", "image_mode": "cached_only"})
        self.assertEqual(pr.main(["--data-dir", str(self.data), "set-project", str(project), "scope", "full"]), 0)
        self.assertEqual(pr.read_json(project), {"scope": "full", "image_mode": "cached_only"})
        self.assertEqual(pr.main(["--data-dir", str(self.data), "set", "focus", ""]), 0)
        self.assertEqual(pr.resolve(self.data)["settings"]["focus"], [])

    def test_persona_can_be_shared_and_changed_without_character_change(self):
        self.register()
        pr.register_character(self.data, "other", {"name": "other", "appearance": "red coat", "persona_id": "calm", "images": {}})
        pr.register_persona(self.data, "direct", {"name": "direct", "style": "short and direct"})
        for key in ("mina", "other"):
            self.assertEqual(pr.resolve(self.data, explicit={"character_id": key})["persona"]["style"], "calm and precise")
            result = pr.resolve(self.data, explicit={"character_id": key, "persona_id": "direct"})
            self.assertEqual(result["settings"]["character_id"], key)
            self.assertEqual(result["persona"]["style"], "short and direct")
            self.assertEqual(pr.load_config(self.data)["characters"][key]["persona_id"], "calm")

    def test_persona_selection_precedence_and_default_reset(self):
        self.register()
        pr.register_persona(self.data, "direct", {"name": "direct", "style": "direct"})
        pr.update(self.data, lambda doc: doc.update(allow_project_overrides=True, settings={"character_id": "mina", "persona_id": "direct"}))
        project = self.root / "project.json"
        pr.atomic_json(project, {"persona_id": "guide"})
        self.assertEqual(pr.resolve(self.data)["settings"]["persona_id"], "direct")
        self.assertEqual(pr.resolve(self.data, project)["settings"]["persona_id"], "guide")
        result = pr.resolve(self.data, project, {"persona_id": "default"})
        self.assertEqual(result["settings"]["persona_id"], "calm")

    def test_unknown_personas_and_invalid_character_binding_preserve_config(self):
        self.register()
        before = (self.data / "config.json").read_bytes()
        with self.assertRaises(pr.Invalid):
            pr.resolve(self.data, explicit={"persona_id": "unknown"})
        with self.assertRaises(pr.Invalid):
            pr.register_character(self.data, "bad", pr.GUIDE_V2 | {"persona_id": "unknown"})
        with self.assertRaises(pr.Invalid):
            pr.register_persona(self.data, "default", {"name": "reserved", "style": ""})
        self.assertEqual((self.data / "config.json").read_bytes(), before)

    def test_invalid_character_definition_reports_error_without_writing(self):
        for definition in (None, 3, [], "not an object"):
            with self.subTest(definition=definition), self.assertRaises(pr.Invalid):
                pr.register_character(self.data, "mina", definition)
        self.assertFalse((self.data / "config.json").exists())

    def test_persona_switch_and_style_update_reuse_images_without_generation(self):
        self.register()
        source = self.image()
        pr.import_image(self.data, "mina", "reference", source)
        pr.import_image(self.data, "mina", "stern", source)
        baseline = pr.visual_plan(self.data, pr.resolve(self.data, explicit={"character_id": "mina"}), helpers.review(severity="high"))
        pr.register_persona(self.data, "direct", {"name": "direct", "style": "harsh"})
        for mode in ("hybrid", "cached_only"):
            result = pr.visual_plan(self.data, pr.resolve(self.data, explicit={"character_id": "mina", "persona_id": "direct", "image_mode": mode}), helpers.review(severity="high"))
            self.assertEqual(result["record_id"], baseline["record_id"])
        pr.register_persona(self.data, "calm", {"name": "calm", "style": "new voice"})
        self.assertEqual(pr.visual_plan(self.data, pr.resolve(self.data, explicit={"character_id": "mina"}), helpers.review(severity="high"))["record_id"], baseline["record_id"])
        self.assertEqual(len(list((self.data / "image-records").glob("*.json"))), 1)

    def test_cached_only_never_generates_even_forced_or_initial_set(self):
        for with_reference in (False, True):
            if with_reference:
                pr.import_image(self.data, "guide", "reference", self.image())
            resolved = pr.resolve(self.data, explicit={"image_mode": "cached_only"})
            for severity in (None, "low", "high"):
                review = helpers.review(severity=severity)
                before = copy.deepcopy(review)
                plan = pr.visual_plan(self.data, resolved, review)
                self.assertEqual(plan["action"], "unavailable")
                self.assertIn("generation is disabled", plan["reason"])
                self.assertEqual(review, before)
            for mood in pr.EXPRESSIONS:
                self.assertEqual(pr.expression_plan(self.data, resolved, mood, force=True)["action"], "unavailable")
            plan = pr.set_plan(self.data, resolved)
            self.assertEqual(plan["expressions"], ["happy", "neutral", "concerned", "stern"])
            self.assertTrue(all(item["action"] == "unavailable" for item in plan["plans"]))

    def test_cached_only_reuses_valid_images_and_rejects_damaged_images(self):
        source = self.image()
        asset = pr.import_image(self.data, "guide", "stern", source)
        resolved = pr.resolve(self.data, explicit={"image_mode": "cached_only"})
        plan = pr.visual_plan(self.data, resolved, helpers.review(severity="high"))
        self.assertEqual(plan["action"], "use_cached")
        self.assertEqual(pr.expression_plan(self.data, resolved, "stern", force=True)["record_id"], plan["record_id"])
        pr.asset_path(self.data, asset).write_bytes(b"damaged")
        self.assertEqual(pr.visual_plan(self.data, resolved, helpers.review(severity="high"))["action"], "unavailable")
        self.assertTrue(pr.asset_path(self.data, asset).exists())

    def test_legacy_register_is_supported_after_migration_and_keeps_assets(self):
        source = self.image()
        asset = pr.import_image(self.data, "guide", "reference", source)
        pr.register_character(self.data, "guide", pr.GUIDE | {"persona": "legacy update"})
        self.assertEqual(pr.resolve(self.data)["persona"]["style"], "legacy update")
        self.assertEqual(pr.load_config(self.data)["characters"]["guide"]["images"]["reference"], asset)

    def migrated_shared_persona(self):
        doc = pr.initial()
        doc["characters"]["mina"] = pr.GUIDE | {"persona": "old mina voice"}
        self.save(doc)
        pr.update(self.data, lambda value: None)
        identifier = pr.load_config(self.data)["characters"]["mina"]["persona_id"]
        pr.register_character(self.data, "other", pr.GUIDE_V2 | {"persona_id": identifier})
        return identifier

    def test_legacy_edit_detaches_from_migrated_shared_persona(self):
        shared = self.migrated_shared_persona()
        before = copy.deepcopy(pr.load_config(self.data)["personas"][shared])
        asset = pr.import_image(self.data, "mina", "neutral", self.image())
        old_plan = pr.expression_plan(self.data, pr.resolve(self.data, explicit={"character_id": "mina"}), "neutral")
        pr.register_character(self.data, "mina", pr.GUIDE | {"persona": "only mina new voice"})
        doc = pr.load_config(self.data)
        self.assertNotEqual(doc["characters"]["mina"]["persona_id"], shared)
        self.assertEqual(doc["characters"]["other"]["persona_id"], shared)
        self.assertEqual(doc["personas"][shared], before)
        self.assertEqual(pr.resolve(self.data, explicit={"character_id": "mina"})["persona"]["style"], "only mina new voice")
        self.assertEqual(pr.resolve(self.data, explicit={"character_id": "other"})["persona"]["style"], "old mina voice")
        new_plan = pr.expression_plan(self.data, pr.resolve(self.data, explicit={"character_id": "mina"}), "neutral")
        self.assertEqual(new_plan["record_id"], old_plan["record_id"])
        self.assertTrue(pr.asset_path(self.data, asset).exists())

    def test_legacy_registration_preserves_colliding_ids_and_explicit_selection(self):
        stem = pr.legacy_persona_id("mina")
        old = {"name": "separate", "style": "unrelated registered voice"}
        for identifier in (stem, stem + "-1"):
            pr.register_persona(self.data, identifier, old)
        pr.register_character(self.data, "other", pr.GUIDE_V2 | {"persona_id": stem})
        pr.update(self.data, lambda doc: doc["settings"].update(persona_id=stem))
        pr.register_character(self.data, "mina", pr.GUIDE | {"persona": "mina voice"})
        doc = pr.load_config(self.data)
        self.assertEqual(doc["characters"]["mina"]["persona_id"], stem + "-2")
        self.assertEqual(doc["settings"]["persona_id"], stem)
        for identifier in (stem, stem + "-1"):
            self.assertEqual(doc["personas"][identifier], old)
        self.assertEqual(pr.resolve(self.data, explicit={"character_id": "other"})["persona"]["style"], old["style"])
        self.assertEqual(pr.resolve(self.data, explicit={"character_id": "mina", "persona_id": "default"})["persona"]["style"], "mina voice")

    def test_identical_legacy_registration_retains_shared_binding(self):
        shared = self.migrated_shared_persona()
        before = pr.load_config(self.data)
        pr.register_character(self.data, "mina", pr.GUIDE | {"persona": "old mina voice"})
        after = pr.load_config(self.data)
        self.assertEqual(after["characters"]["mina"]["persona_id"], shared)
        self.assertEqual(after["personas"], before["personas"])
        pr.register_character(self.data, "mina", pr.GUIDE | {"persona": "changed"})
        once = pr.load_config(self.data)
        pr.register_character(self.data, "mina", pr.GUIDE | {"persona": "changed"})
        self.assertEqual(pr.load_config(self.data), once)

    def test_shared_legacy_edit_failure_preserves_all_personas_and_retry(self):
        shared = self.migrated_shared_persona()
        before = (self.data / "config.json").read_bytes()
        with patch.object(pr.os, "replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                pr.register_character(self.data, "mina", pr.GUIDE | {"persona": "new voice"})
        self.assertEqual((self.data / "config.json").read_bytes(), before)
        self.assertEqual(list(self.data.glob(".pending-*")), [])
        self.assertFalse((self.data / "config.lock").exists())
        pr.register_character(self.data, "mina", pr.GUIDE | {"persona": "new voice"})
        self.assertEqual(pr.load_config(self.data)["personas"][shared]["style"], "old mina voice")

    def test_legacy_edit_during_v1_migration_keeps_old_persona_and_backup_on_failure(self):
        doc = pr.initial()
        doc["characters"]["mina"] = pr.GUIDE | {"persona": "old voice"}
        self.save(doc)
        before = (self.data / "config.json").read_bytes()
        original_replace = pr.os.replace
        def fail_config(source, destination):
            if destination == self.data / "config.json":
                raise OSError("migration save failure")
            return original_replace(source, destination)
        with patch.object(pr.os, "replace", side_effect=fail_config):
            with self.assertRaises(OSError):
                pr.register_character(self.data, "mina", pr.GUIDE | {"persona": "new voice"})
        self.assertEqual((self.data / "config.json").read_bytes(), before)
        self.assertEqual(next(self.data.glob("config.v1-backup-*")).read_bytes(), before)
        pr.register_character(self.data, "mina", pr.GUIDE | {"persona": "new voice"})
        migrated = pr.load_config(self.data)
        stem = pr.legacy_persona_id("mina")
        self.assertEqual(migrated["personas"][stem]["style"], "old voice")
        self.assertNotEqual(migrated["characters"]["mina"]["persona_id"], stem)
        self.assertEqual(pr.resolve(self.data, explicit={"character_id": "mina"})["persona"]["style"], "new voice")

    def test_file_review_mode_keeps_uncertainty_and_severity_rules(self):
        review = helpers.review(status="partial", severity="high")
        review["scope"]["mode"] = "file"
        pr.validate_review(review)
        self.assertEqual(pr.expression(review), "stern")


if __name__ == "__main__":
    unittest.main()
