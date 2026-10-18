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
        self.assertEqual(resolved["settings"]["image_mode"], "A")
        self.assertTrue(resolved["project_ignored"])
        doc["allow_project_overrides"] = True
        self.save(doc)
        resolved = pr.resolve(self.data, project, {"image_mode": "hybrid"})
        self.assertEqual(resolved["settings"], {"character": "guide", "image_mode": "hybrid", "review_mode": "diff"})
        self.assertEqual(resolved["sources"], {"character": "bundled", "image_mode": "explicit", "review_mode": "project"})

    def test_disabled_project_is_not_read(self):
        self.assertTrue(pr.resolve(self.data, self.root / "does-not-exist")["project_ignored"])

    def test_project_update_preserves_other_settings_and_rejects_unknown(self):
        project = self.root / ".persona-review.json"
        pr.atomic_json(project, {"review_mode": "diff"})
        self.assertEqual(pr.main(["--data-dir", str(self.data), "set-project", str(project), "image_mode", "B"]), 0)
        self.assertEqual(pr.read_json(project), {"review_mode": "diff", "image_mode": "B"})
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
        for mode, expected in (("A", "generate"), ("B", "use_registered"), ("hybrid", "use_registered")):
            resolved = pr.resolve(self.data, explicit={"image_mode": mode})
            plan = pr.visual_plan(self.data, resolved, review(severity="high"))
            self.assertEqual(plan["action"], expected)
            self.assertNotIn("private-code-canary", json.dumps(plan))
            self.assertNotIn("persona", plan)
        (self.data / "assets" / registered).unlink()
        self.assertEqual(pr.visual_plan(self.data, pr.resolve(self.data), review(severity="high"))["action"], "generate")
        (self.data / "assets" / reference).unlink()
        self.assertEqual(pr.visual_plan(self.data, pr.resolve(self.data), review(severity="high"))["action"], "unavailable")

    def test_missing_b_image_does_not_generate(self):
        resolved = pr.resolve(self.data, explicit={"image_mode": "B"})
        self.assertEqual(pr.visual_plan(self.data, resolved, review())["action"], "unavailable")

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
