#!/usr/bin/env python3
"""Offline configuration and visual planning; never executes reviewed code."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
MAX_JSON_BYTES = 2_000_000
DEFAULTS = {"character": "guide", "image_mode": "hybrid", "review_mode": "auto"}
GUIDE = {"name": "ガイド", "persona": "穏やかで簡潔な日本語。根拠を添えて話す。", "appearance": "", "images": {}}
EXPRESSIONS = ("happy", "neutral", "concerned", "stern")


class Invalid(ValueError):
    pass


def read_json(path):
    # Bound inputs and reject duplicate keys, which otherwise hide overwritten values.
    with open(path, "rb") as handle:
        data = handle.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise Invalid(f"JSON exceeds {MAX_JSON_BYTES} bytes")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise Invalid(f"duplicate key: {key}")
            result[key] = value
        return result
    return json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique)


def schema(name):
    return read_json(ROOT / "schemas" / f"{name}.schema.json")


def validate(value, rule, location="$"):
    """Validate the deliberately small JSON Schema subset used by this package."""
    supported = {"$schema", "type", "required", "properties", "additionalProperties", "items",
                 "enum", "pattern", "minLength", "maxLength", "minimum"}
    if set(rule) - supported:
        raise Invalid("unsupported schema keyword")
    kind = rule.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
    if kind not in types or type(value) is not types[kind]:
        raise Invalid(f"{location}: expected {kind}")
    if "enum" in rule and value not in rule["enum"]:
        raise Invalid(f"{location}: invalid choice")
    if kind == "object":
        props = rule.get("properties", {})
        if set(rule.get("required", [])) - value.keys():
            raise Invalid(f"{location}: missing required field")
        if rule.get("additionalProperties") is False and value.keys() - props.keys():
            raise Invalid(f"{location}: unknown field")
        for key, child in value.items():
            if key in props:
                validate(child, props[key], f"{location}.{key}")
            elif type(rule.get("additionalProperties")) is dict:
                validate(child, rule["additionalProperties"], f"{location}.{key}")
    elif kind == "array":
        for index, child in enumerate(value):
            validate(child, rule["items"], f"{location}[{index}]")
    elif kind == "string":
        if len(value) < rule.get("minLength", 0) or len(value) > rule.get("maxLength", float("inf")):
            raise Invalid(f"{location}: invalid text length")
        if "pattern" in rule and not re.fullmatch(rule["pattern"], value):
            raise Invalid(f"{location}: invalid identifier")
    elif kind == "integer" and value < rule.get("minimum", -float("inf")):
        raise Invalid(f"{location}: value below minimum")


def character_id(value):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", value):
        raise Invalid("invalid character id")
    return value


def initial():
    return {"version": 1, "allow_project_overrides": False, "settings": {}, "characters": {}}


def validate_config(doc):
    validate(doc, schema("config"))
    if type(doc) is not dict or set(doc) != {"version", "allow_project_overrides", "settings", "characters"}:
        raise Invalid("invalid config fields")
    if type(doc["version"]) is not int or doc["version"] != 1 or type(doc["allow_project_overrides"]) is not bool:
        raise Invalid("invalid config version or project policy")
    validate(doc["settings"], schema("settings"))
    if type(doc["characters"]) is not dict:
        raise Invalid("characters must be an object")
    for key, value in doc["characters"].items():
        character_id(key)
        validate(value, schema("character"))
    selected = doc["settings"].get("character", "guide")
    if selected != "guide" and selected not in doc["characters"]:
        raise Invalid("default character is not registered")


def default_data_dir():
    # Override is a local CLI/environment choice, never read from project configuration.
    if os.environ.get("PERSONA_REVIEW_HOME"):
        return Path(os.environ["PERSONA_REVIEW_HOME"]).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "persona-review"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "persona-review"


def load_config(data_dir):
    path = Path(data_dir) / "config.json"
    doc = read_json(path) if path.exists() else initial()
    validate_config(doc)
    return doc


@contextmanager
def locked(data_dir, name="config.lock"):
    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise Invalid("config is locked; retry later (after a crash, inspect and remove config.lock)") from exc
    try:
        os.close(fd)
        yield
    finally:
        path.unlink()


def atomic_json(path, doc):
    data = (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(data) > MAX_JSON_BYTES:
        raise Invalid(f"JSON exceeds {MAX_JSON_BYTES} bytes")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def update(data_dir, mutation):
    with locked(data_dir):
        doc = load_config(data_dir)
        mutation(doc)
        validate_config(doc)
        atomic_json(Path(data_dir) / "config.json", doc)


def resolve(data_dir, project=None, explicit=None):
    doc = load_config(data_dir)
    settings = DEFAULTS | doc["settings"]
    sources = {key: "global" if key in doc["settings"] else "bundled" for key in DEFAULTS}
    ignored = bool(project and not doc["allow_project_overrides"])
    if project and doc["allow_project_overrides"]:
        overrides = read_json(project)
        validate(overrides, schema("settings"))
        settings.update(overrides)
        sources.update({key: "project" for key in overrides})
    overrides = explicit or {}
    validate(overrides, schema("settings"))
    settings.update(overrides)
    sources.update({key: "explicit" for key in overrides})
    key = settings["character"]
    character = doc["characters"].get(key, GUIDE if key == "guide" else None)
    if character is None:
        raise Invalid(f"unregistered character: {key}")
    return {"settings": settings, "sources": sources, "project_ignored": ignored, "character": copy.deepcopy(character)}


def asset_path(data_dir, filename):
    if not re.fullmatch(r"[0-9a-f]{32}\.(png|jpg|webp)", filename):
        raise Invalid("invalid asset name")
    root = Path(data_dir).resolve()
    path = (root / "assets" / filename).resolve()
    if not path.is_relative_to(root) or path.parent != root / "assets":
        raise Invalid("asset escapes managed directory")
    return path


def image_type(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    raise Invalid("expected a PNG, JPEG, or WebP image")


def import_image(data_dir, key, slot, source):
    character_id(key)
    if slot not in ("reference", *EXPRESSIONS):
        raise Invalid("invalid image slot")
    with open(source, "rb") as handle:
        data = handle.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise Invalid("image exceeds 10 MiB")
    suffix = image_type(data)
    with locked(data_dir):
        doc = load_config(data_dir)
        if key not in doc["characters"]:
            if key != "guide":
                raise Invalid("register the character first")
            doc["characters"][key] = copy.deepcopy(GUIDE)
        filename = f"{uuid.uuid4().hex}.{suffix}"
        dest = asset_path(data_dir, filename)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Immutable assets prevent a failed config save from altering the current image.
        with open(dest, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        doc["characters"][key]["images"][slot] = filename
        validate_config(doc)
        atomic_json(Path(data_dir) / "config.json", doc)
    return filename


def validate_review(review):
    validate(review, schema("review"))
    scope = review["scope"]
    if scope["status"] == "complete" and (not scope["reviewed"] or scope["unverified"]):
        raise Invalid("complete scope requires reviewed content and no unverified items")
    if scope["status"] in ("empty", "unavailable") and (scope["reviewed"] or review["findings"] or review["strengths"]):
        raise Invalid("empty/unavailable review cannot claim examined content or findings")
    ids = [item["id"] for item in review["findings"]]
    if len(ids) != len(set(ids)):
        raise Invalid("finding ids must be unique")


def expression(review):
    validate_review(review)
    levels = {item["severity"] for item in review["findings"]}
    if levels & {"critical", "high"}:
        return "stern"
    if levels & {"medium", "low"}:
        return "concerned"
    if review["scope"]["status"] != "complete":
        return "neutral"
    return "happy"


def visual_plan(data_dir, resolved, review):
    mood = expression(review)
    mode = resolved["settings"]["image_mode"]
    character = resolved["character"]
    def available(slot):
        name = character["images"].get(slot)
        if name:
            path = asset_path(data_dir, name)
            if path.is_file():
                return str(path)
        return None
    registered = available(mood) if mode in ("B", "hybrid") else None
    if registered:
        return {"action": "use_registered", "expression": mood, "image_path": registered}
    if mode == "B":
        return {"action": "unavailable", "expression": mood, "reason": "matching expression image is not registered"}
    reference = available("reference")
    if not reference:
        return {"action": "unavailable", "expression": mood, "reason": "reference image is not registered"}
    # This is data for Codex, not a tool call or executable prompt. No review text is included.
    return {"action": "generate", "expression": mood, "reference_path": reference,
            "appearance_data": character["appearance"], "generator": "Codex image_gen"}


def main(argv=None):
    # Stable JSON encoding for Windows redirected output, including character names.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show")
    setting = sub.add_parser("set")
    setting.add_argument("key", choices=[*DEFAULTS, "allow_project_overrides"])
    setting.add_argument("value")
    project_setting = sub.add_parser("set-project")
    project_setting.add_argument("path", type=Path)
    project_setting.add_argument("key", choices=list(DEFAULTS))
    project_setting.add_argument("value")
    register = sub.add_parser("register")
    register.add_argument("id")
    register.add_argument("definition", type=Path)
    imp = sub.add_parser("import-image")
    imp.add_argument("id")
    imp.add_argument("slot", choices=["reference", *EXPRESSIONS])
    imp.add_argument("source", type=Path)
    for name in ("resolve", "plan"):
        command = sub.add_parser(name)
        command.add_argument("--project", type=Path)
        for key in DEFAULTS:
            command.add_argument("--" + key.replace("_", "-"))
        if name == "plan":
            command.add_argument("review", type=Path)
    check = sub.add_parser("validate-review")
    check.add_argument("review", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "show":
            result = load_config(args.data_dir)
        elif args.command == "set":
            def change(doc):
                if args.key == "allow_project_overrides":
                    if args.value not in ("true", "false"):
                        raise Invalid("use true or false")
                    doc[args.key] = args.value == "true"
                else:
                    doc["settings"][args.key] = args.value
            update(args.data_dir, change)
            result = {"saved": True}
        elif args.command == "set-project":
            if args.path.name != ".persona-review.json":
                raise Invalid("project filename must be .persona-review.json")
            with locked(args.path.parent, ".persona-review.lock"):
                overrides = read_json(args.path) if args.path.exists() else {}
                validate(overrides, schema("settings"))
                overrides[args.key] = args.value
                resolve(args.data_dir, explicit=overrides)
                atomic_json(args.path, overrides)
            result = {"saved": True, "project_overrides_enabled": load_config(args.data_dir)["allow_project_overrides"]}
        elif args.command == "register":
            key = character_id(args.id)
            definition = read_json(args.definition)
            validate(definition, schema("character"))
            # Imports are the only way to attach files; preserve existing managed images.
            if definition["images"]:
                raise Invalid("register requires empty images; use import-image")
            def change(doc):
                definition["images"] = doc["characters"].get(key, {}).get("images", {})
                doc["characters"][key] = definition
            update(args.data_dir, change)
            result = {"registered": key}
        elif args.command == "import-image":
            result = {"asset": import_image(args.data_dir, args.id, args.slot, args.source)}
        elif args.command == "validate-review":
            validate_review(read_json(args.review))
            result = {"valid": True}
        else:
            explicit = {key: getattr(args, key) for key in DEFAULTS if getattr(args, key) is not None}
            resolved = resolve(args.data_dir, args.project, explicit)
            result = resolved if args.command == "resolve" else visual_plan(args.data_dir, resolved, read_json(args.review))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (Invalid, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"persona-review: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
