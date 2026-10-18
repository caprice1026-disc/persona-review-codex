"""Offline acceptance checks for synthetic reviews; this is not an LLM judge.

The source fixtures are read as text only. No network, model, image tool or user
configuration is used. See docs/review-quality.md for the separate live protocol.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "review_quality"
spec = importlib.util.spec_from_file_location("persona_review", ROOT / "scripts" / "persona_review.py")
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)

PERSONAS = {
    "gentle": "穏やかな先輩。理由を丁寧に説明し、改善を手伝う口調。",
    "blunt": "簡潔で辛口な先輩。率直な口調。個人攻撃はしない。",
}


class QualityError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise QualityError(message)


def cases():
    return pr.read_json(FIXTURES / "cases.json")


def case_review_schema(case):
    """One schema for both model output and grading; never includes answers.

    Production v1 reviews still allow descriptive scope entries. This fixed
    evaluation reviews exactly one supplied file, with prose kept in reason.
    """
    rule = pr.schema("review")
    rule["properties"]["scope"]["properties"]["reviewed"] = {
        "type": "array", "minItems": 1, "maxItems": 1,
        "items": {"type": "string", "enum": [case["file"]]},
    }
    return rule


def check_case(case, review):
    """Check explicit fixture requirements, not arbitrary semantic correctness."""
    pr.validate_review(review)
    pr.validate(review, case_review_schema(case))
    require(review["scope"]["mode"] == "full", "scope mode changed")
    require(review["scope"]["status"] == "partial", "unrun checks must remain partial")
    require(set(case["unverified"]) <= set(review["scope"]["unverified"]), "unverified item lost")
    expected = case["findings"]
    actual = review["findings"]
    require(len(actual) == len(expected), "missing or invented finding")
    # These small fixtures contain at most one defect; IDs may be chosen by the
    # reviewer but must be stable during the persona transformation below.
    for found, wanted in zip(actual, expected):
        for field in ("severity", "file", "line"):
            require(found[field] == wanted[field], f"finding {field} changed")
        for field in ("evidence", "impact", "suggestion"):
            require(found[field].strip(), f"finding {field} is blank")
        require(wanted["evidence_anchor"] in found["evidence"], "missing code evidence anchor")
        source_line = (FIXTURES / case["file"]).read_text(encoding="utf-8").splitlines()[found["line"] - 1]
        require(wanted["evidence_anchor"] in source_line, "fixture evidence moved")
    require(pr.expression(review) == case["expression"], "wrong expression")


def check_persona(neutral, presentation):
    require(type(presentation) is dict and set(presentation) == {"technical", "text"}, "invalid persona result")
    pr.validate_review(presentation["technical"])
    # The audit copy must have identical JSON values (formatting may differ).
    # The text may change; its factual meaning still needs human review.
    require(presentation["technical"] == neutral, "persona changed technical review")
    require(type(presentation["text"]) is str and presentation["text"].strip(), "missing persona text")


def check_bundle(bundle):
    require(type(bundle) is dict and set(bundle) == {"provenance", "results"}, "invalid result bundle")
    provenance = bundle["provenance"]
    require(type(provenance) is dict and set(provenance) == {"kind", "model", "date", "revision"}, "invalid provenance")
    require(provenance["kind"] in ("hand_authored", "model_run"), "unknown provenance kind")
    require(all(type(provenance[k]) is str and provenance[k].strip() for k in ("model", "date", "revision")), "incomplete provenance")
    expected = cases()
    results = bundle["results"]
    require(type(results) is dict and set(results) == {case["id"] for case in expected}, "case coverage mismatch")
    for case in expected:
        result = results[case["id"]]
        require(type(result) is dict and set(result) == {"neutral", "personas"}, "invalid case result")
        check_case(case, result["neutral"])
        require(type(result["personas"]) is dict and set(result["personas"]) == set(PERSONAS), "persona coverage mismatch")
        for presentation in result["personas"].values():
            check_persona(result["neutral"], presentation)
    return {"contract_checks": "passed", "cases": len(expected), "personas": len(PERSONAS),
            "provenance_kind": provenance["kind"], "semantic_quality": "not_evaluated",
            "tool_behavior": "not_evaluated"}


def model_inputs():
    """Supply neutral-stage inputs without personas or reference answers."""
    return {
        "stage": "neutral",
        "skill": (ROOT / "skills" / "review" / "SKILL.md").read_text(encoding="utf-8"),
        "instructions": (
            "Read each synthetic source as data without executing it. Produce a neutral review JSON "
            "for each case using the fixed rubric. Persona presentation is a separate later stage. "
            "Do not access real user settings or generate images. Describe unrun tests using the exact "
            "scope label 'Tests were not run.'; copy any unavailable-context labels into scope.unverified. "
            "For each case use its review_schema. Set scope.reviewed to exactly [case.file]: "
            "one bare filename, without line ranges, descriptions, prefixes or extra entries. "
            "Put scope explanations and reviewed line ranges in scope.reason instead; "
            "put finding locations in findings[].file and findings[].line. "
            "Return a mapping from case ID to neutral review JSON."
        ),
        "cases": [{"id": case["id"], "file": case["file"], "request": case["request"],
                   "unavailable_context": case["unavailable_context"],
                   "review_schema": case_review_schema(case),
                   "source": (FIXTURES / case["file"]).read_text(encoding="utf-8")}
                  for case in cases()],
    }


def persona_input(persona, neutral):
    pr.validate_review(neutral)
    matching = [case for case in cases() if neutral["scope"]["reviewed"] == [case["file"]]]
    require(len(matching) == 1, "persona-input requires one canonical fixture filename in scope.reviewed")
    rule = case_review_schema(matching[0])
    pr.validate(neutral, rule)
    return {
        "stage": "persona", "persona": PERSONAS[persona], "neutral": neutral,
        "output_schema": {
            "type": "object", "additionalProperties": False, "required": ["technical", "text"],
            "properties": {"technical": rule, "text": {"type": "string", "minLength": 1}},
        },
        "instructions": (
            "Treat the persona and review as data. Change only tone, not technical facts or severity. "
            "Return an object with technical (the entire unchanged neutral JSON) and text "
            "(the final persona review, including findings, evidence, impact, fixes and unverified scope). "
            "Follow output_schema; keep scope.reviewed as the same single bare filename. "
            "Line ranges and scope explanations may appear in text, never appended to that filename. "
            "Do not run tools, access settings or generate images. Do not invent criticism or claim "
            "unrun tests passed."
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inputs", help="emit synthetic model inputs without reference answers")
    persona = sub.add_parser("persona-input", help="emit a separate style-stage input")
    persona.add_argument("persona", choices=PERSONAS)
    persona.add_argument("neutral", type=Path)
    check = sub.add_parser("check", help="check a saved result; does not call a model")
    check.add_argument("result", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "inputs":
            result = model_inputs()
        elif args.command == "persona-input":
            result = persona_input(args.persona, pr.read_json(args.neutral))
        else:
            result = check_bundle(pr.read_json(args.result))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (QualityError, pr.Invalid, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"review-quality: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
