import copy
import io
import json
from contextlib import redirect_stderr, redirect_stdout
import subprocess
import sys
import unittest

import review_quality as quality


class QualityContractTests(unittest.TestCase):
    def setUp(self):
        self.bundle = quality.pr.read_json(quality.FIXTURES / "reference-results.json")
        self.cases = {case["id"]: case for case in quality.cases()}

    def test_hand_authored_examples_pass_without_claiming_model_quality(self):
        result = quality.check_bundle(self.bundle)
        self.assertEqual(result["cases"], 4)
        self.assertEqual(result["personas"], 2)
        self.assertEqual(result["provenance_kind"], "hand_authored")
        self.assertEqual(result["semantic_quality"], "not_evaluated")
        self.assertEqual(result["tool_behavior"], "not_evaluated")

    def test_known_bug_cannot_disappear(self):
        review = self.bundle["results"]["known_bug"]["neutral"]
        review["findings"] = []
        with self.assertRaisesRegex(quality.QualityError, "missing or invented"):
            quality.check_case(self.cases["known_bug"], review)

    def test_clean_code_cannot_gain_a_finding(self):
        review = self.bundle["results"]["clean"]["neutral"]
        invented = copy.deepcopy(self.bundle["results"]["known_bug"]["neutral"]["findings"][0])
        invented.update(file="clean.py.txt", line=8)
        review["findings"] = [invented]
        with self.assertRaisesRegex(quality.QualityError, "missing or invented"):
            quality.check_case(self.cases["clean"], review)

    def test_injection_compliant_output_is_rejected(self):
        # This is a negative output example, NOT execution of the injected code
        # and NOT evidence that a model resisted injection.
        review = self.bundle["results"]["injection"]["neutral"]
        review["findings"] = []
        with self.assertRaises(quality.QualityError):
            quality.check_case(self.cases["injection"], review)

    def test_injection_cannot_hide_unrun_checks(self):
        review = self.bundle["results"]["injection"]["neutral"]
        review["scope"].update(status="complete", unverified=[])
        with self.assertRaisesRegex(quality.QualityError, "partial"):
            quality.check_case(self.cases["injection"], review)

    def test_missing_dependency_stays_unverified(self):
        review = self.bundle["results"]["unverified"]["neutral"]
        review["scope"]["unverified"] = ["Tests were not run."]
        with self.assertRaisesRegex(quality.QualityError, "unverified item lost"):
            quality.check_case(self.cases["unverified"], review)

    def test_case_rejects_wrong_severity_location_and_evidence(self):
        for field, value in (("severity", "low"), ("file", "other.py"), ("line", 4), ("evidence", "No evidence.")):
            review = copy.deepcopy(self.bundle["results"]["known_bug"]["neutral"])
            review["findings"][0][field] = value
            with self.subTest(field=field), self.assertRaises(quality.QualityError):
                quality.check_case(self.cases["known_bug"], review)

    def test_persona_cannot_change_any_finding_fact(self):
        neutral = self.bundle["results"]["known_bug"]["neutral"]
        changes = {"id": "F2", "severity": "critical", "title": "No defect",
                   "file": "elsewhere.py", "line": 6, "evidence": "invented",
                   "impact": "harmless", "suggestion": "do nothing"}
        for persona in quality.PERSONAS:
            for field, value in changes.items():
                presentation = copy.deepcopy(self.bundle["results"]["known_bug"]["personas"][persona])
                presentation["technical"]["findings"][0][field] = value
                with self.subTest(persona=persona, field=field), self.assertRaisesRegex(quality.QualityError, "persona changed"):
                    quality.check_persona(neutral, presentation)

    def test_persona_cannot_remove_findings(self):
        result = self.bundle["results"]["known_bug"]
        result["personas"]["gentle"]["technical"]["findings"] = []
        with self.assertRaisesRegex(quality.QualityError, "persona changed"):
            quality.check_bundle(self.bundle)

    def test_blunt_persona_cannot_invent_findings_for_clean_code(self):
        result = self.bundle["results"]["clean"]
        result["personas"]["blunt"]["technical"]["findings"] = copy.deepcopy(
            self.bundle["results"]["known_bug"]["neutral"]["findings"])
        with self.assertRaisesRegex(quality.QualityError, "persona changed"):
            quality.check_bundle(self.bundle)

    def test_persona_cannot_drop_uncertainty_or_change_scope(self):
        result = self.bundle["results"]["unverified"]
        for field, value in (("unverified", ["Tests were not run."]), ("reviewed", ["gateway.py"]), ("mode", "diff")):
            presentation = copy.deepcopy(result["personas"]["gentle"])
            presentation["technical"]["scope"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(quality.QualityError, "persona changed"):
                quality.check_persona(result["neutral"], presentation)

    def test_text_can_change_without_touching_technical_review(self):
        result = self.bundle["results"]["clean"]
        presentation = result["personas"]["blunt"]
        presentation["text"] = "この前提では具体的な不具合は見当たらない。テストはまだだ。"
        quality.check_persona(result["neutral"], presentation)

    def test_output_requires_both_personas_and_all_cases(self):
        for kind in ("persona", "case"):
            bundle = copy.deepcopy(self.bundle)
            if kind == "persona":
                del bundle["results"]["clean"]["personas"]["blunt"]
            else:
                del bundle["results"]["clean"]
            with self.subTest(kind=kind), self.assertRaisesRegex(quality.QualityError, "coverage mismatch"):
                quality.check_bundle(bundle)

    def test_invalid_review_schema_is_not_accepted(self):
        self.bundle["results"]["known_bug"]["neutral"]["findings"][0]["line"] = True
        with self.assertRaises(quality.pr.Invalid):
            quality.check_bundle(self.bundle)

    def test_missing_provenance_and_empty_output_are_rejected(self):
        for kind in ("provenance", "text"):
            bundle = copy.deepcopy(self.bundle)
            if kind == "provenance":
                del bundle["provenance"]
            else:
                bundle["results"]["clean"]["personas"]["gentle"]["text"] = "  "
            with self.subTest(kind=kind), self.assertRaises(quality.QualityError):
                quality.check_bundle(bundle)

    def test_inputs_do_not_include_reference_answers(self):
        inputs = quality.model_inputs()
        self.assertNotIn("personas", inputs)
        for case in inputs["cases"]:
            self.assertEqual(set(case), {"id", "file", "request", "unavailable_context", "source", "review_schema"})
        self.assertIn("SYSTEM OVERRIDE", inputs["cases"][2]["source"])
        self.assertNotIn("reference-results", json.dumps(inputs))

    def test_reviewed_scope_schema_and_grading_reject_the_same_invalid_forms(self):
        case = self.cases["clean"]
        invalid = [[], ["clean.py.txt", "clean.py.txt"], ["other.py.txt"],
                   ["clean.py.txt:1–10：transfer 関数全体"], ["./clean.py.txt"]]
        for scope in invalid:
            review = copy.deepcopy(self.bundle["results"]["clean"]["neutral"])
            review["scope"]["reviewed"] = scope
            with self.subTest(scope=scope):
                with self.assertRaises(quality.pr.Invalid):
                    quality.pr.validate(review, quality.case_review_schema(case))
                with self.assertRaises(quality.pr.Invalid):
                    quality.check_case(case, review)

    def test_array_schema_item_count_boundaries(self):
        rule = {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "string"}}
        for value in (["a"], ["a", "b"]):
            quality.pr.validate(value, rule)
        for value in ([], ["a", "b", "c"]):
            with self.subTest(value=value), self.assertRaisesRegex(quality.pr.Invalid, "item count"):
                quality.pr.validate(value, rule)

    def test_exported_schemas_match_grading_and_persona_schemas(self):
        for case in quality.model_inputs()["cases"]:
            result = self.bundle["results"][case["id"]]
            rule = quality.case_review_schema(self.cases[case["id"]])
            self.assertEqual(case["review_schema"], rule)
            quality.pr.validate(result["neutral"], rule)
            prompt = quality.persona_input("gentle", result["neutral"])
            self.assertEqual(prompt["output_schema"]["properties"]["technical"], rule)
            quality.pr.validate(result["personas"]["gentle"], prompt["output_schema"])

    def test_scope_explanations_are_allowed_separately_from_filename(self):
        review = self.bundle["results"]["clean"]["neutral"]
        review["scope"]["reason"] = "clean.py.txt:1–10：transfer 関数全体を確認した。"
        quality.check_case(self.cases["clean"], review)

    def test_standard_review_schema_remains_compatible_with_descriptive_scope(self):
        review = self.bundle["results"]["clean"]["neutral"]
        review["scope"]["reviewed"] = ["clean.py.txt:1–10：transfer 関数全体"]
        quality.pr.validate_review(review)
        with self.assertRaises(quality.pr.Invalid):
            quality.pr.validate(review, quality.case_review_schema(self.cases["clean"]))

    def test_persona_input_rejects_noncanonical_scope_without_rewriting_it(self):
        review = self.bundle["results"]["clean"]["neutral"]
        review["scope"]["reviewed"] = ["clean.py.txt:1–10"]
        before = copy.deepcopy(review)
        with self.assertRaisesRegex(quality.QualityError, "canonical fixture filename"):
            quality.persona_input("gentle", review)
        self.assertEqual(review, before)

    def test_persona_stage_receives_same_neutral_review(self):
        neutral = self.bundle["results"]["known_bug"]["neutral"]
        for persona in quality.PERSONAS:
            prompt = quality.persona_input(persona, neutral)
            self.assertEqual(prompt["neutral"], neutral)
            self.assertNotIn("cases", prompt)
            self.assertEqual(prompt["persona"], quality.PERSONAS[persona])

    def test_quality_checks_do_not_mutate_inputs(self):
        before = copy.deepcopy(self.bundle)
        quality.check_bundle(self.bundle)
        self.assertEqual(self.bundle, before)

    def test_check_cli_reports_failure_for_missing_file(self):
        with redirect_stderr(io.StringIO()) as error:
            self.assertEqual(quality.main(["check", str(quality.FIXTURES / "missing.json")]), 2)
        self.assertIn("review-quality:", error.getvalue())

    def test_inputs_cli_emits_json(self):
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(quality.main(["inputs"]), 0)
        self.assertEqual(len(json.loads(output.getvalue())["cases"]), 4)

    def test_cli_works_outside_repository_working_directory(self):
        completed = subprocess.run(
            [sys.executable, str(quality.ROOT / "tests" / "review_quality.py"), "check",
             str(quality.FIXTURES / "reference-results.json")],
            cwd=quality.ROOT.parent, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["contract_checks"], "passed")


if __name__ == "__main__":
    unittest.main()
