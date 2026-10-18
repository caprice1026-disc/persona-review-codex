#!/usr/bin/env python3
"""Offline configuration and visual planning; never executes reviewed code."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import hashlib
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
MODE_ALIASES = {"A": "always", "B": "pre_generate"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024


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
        migrate_catalog_locked(data_dir, doc)
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
    settings["image_mode"] = MODE_ALIASES.get(settings["image_mode"], settings["image_mode"])
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


def image_bytes(path):
    with open(path, "rb") as handle:
        data = handle.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise Invalid("image exceeds 10 MiB")
    image_type(data)
    return data


def digest(data):
    return hashlib.sha256(data).hexdigest()


def object_digest(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def image_context(data_dir, key, character):
    reference = character["images"].get("reference", "")
    reference_hash = "none"
    if reference:
        try:
            reference_hash = digest(image_bytes(asset_path(data_dir, reference)))
        except (OSError, Invalid):
            reference_hash = "missing"
    identity = {"name": character["name"], "appearance": character["appearance"]}
    fields = {"character_id": key, "character_fingerprint": object_digest(identity),
              "reference_sha256": reference_hash}
    return fields | {"context_key": object_digest(fields), "reference_asset": reference}


def record_path(data_dir, record_id):
    if not re.fullmatch(r"[0-9a-f]{32}", record_id):
        raise Invalid("invalid image record id")
    root = Path(data_dir).resolve()
    path = (root / "image-records" / f"{record_id}.json").resolve()
    if path.parent != root / "image-records":
        raise Invalid("image record escapes managed directory")
    return path


def validate_context(value):
    fields = {key: value[key] for key in ("character_id", "character_fingerprint", "reference_sha256")}
    character_id(fields["character_id"])
    if object_digest(fields) != value["context_key"]:
        raise Invalid("image context key does not match its provenance")


def make_record(record_id, context, mood, asset, data, origin):
    return {"version": 1, "id": record_id, **context, "expression": mood, "asset": asset,
            "image_sha256": digest(data), "origin": origin, "usable": True,
            "created_at": datetime.now(timezone.utc).isoformat()}


def save_record(data_dir, record):
    validate(record, schema("image-record"))
    validate_context(record)
    atomic_json(record_path(data_dir, record["id"]), record)


def migrate_catalog_locked(data_dir, doc):
    marker = Path(data_dir) / "image-catalog.json"
    if marker.exists():
        if read_json(marker) != {"version": 1}:
            raise Invalid("unsupported image catalog version")
        return
    # Capture legacy bindings before a reference or character edit can change their identity.
    for key, character in doc["characters"].items():
        context = image_context(data_dir, key, character)
        for mood in EXPRESSIONS:
            asset = character["images"].get(mood)
            if not asset:
                continue
            try:
                data = image_bytes(asset_path(data_dir, asset))
            except (OSError, Invalid):
                continue
            record_id = object_digest([key, mood, asset, context["context_key"]])[:32]
            if not record_path(data_dir, record_id).exists():
                save_record(data_dir, make_record(record_id, context, mood, asset, data, "legacy"))
    atomic_json(marker, {"version": 1})


def migrate_catalog(data_dir):
    with locked(data_dir):
        migrate_catalog_locked(data_dir, load_config(data_dir))


def cached_image(data_dir, context, mood):
    if context["reference_sha256"] == "missing":
        return None
    candidates = []
    directory = record_path(data_dir, "0" * 32).parent
    for path in directory.glob("*.json"):
        try:
            record = read_json(record_path(data_dir, path.stem))
            validate(record, schema("image-record"))
            validate_context(record)
            if record["id"] != path.stem:
                continue
            if record["usable"] and record["context_key"] == context["context_key"] and record["expression"] == mood:
                candidates.append(record)
        except (Invalid, OSError, UnicodeError, json.JSONDecodeError):
            continue
    for record in sorted(candidates, key=lambda item: (item["created_at"], item["id"]), reverse=True):
        try:
            path = asset_path(data_dir, record["asset"])
            if digest(image_bytes(path)) == record["image_sha256"]:
                return {"action": "use_cached", "expression": mood, "image_path": str(path),
                        "record_id": record["id"], "context_key": context["context_key"], "origin": record["origin"]}
        except (Invalid, OSError):
            continue
    return None


def write_asset(data_dir, data):
    filename = f"{uuid.uuid4().hex}.{image_type(data)}"
    dest = asset_path(data_dir, filename)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return filename


def import_image(data_dir, key, slot, source):
    character_id(key)
    if slot not in ("reference", *EXPRESSIONS):
        raise Invalid("invalid image slot")
    data = image_bytes(source)
    with locked(data_dir):
        doc = load_config(data_dir)
        migrate_catalog_locked(data_dir, doc)
        if key not in doc["characters"]:
            if key != "guide":
                raise Invalid("register the character first")
            doc["characters"][key] = copy.deepcopy(GUIDE)
        filename = write_asset(data_dir, data)
        if slot == "reference":
            doc["characters"][key]["images"][slot] = filename
            validate_config(doc)
            atomic_json(Path(data_dir) / "config.json", doc)
        else:
            # The catalog is authoritative for expressions; commit only one binding.
            context = image_context(data_dir, key, doc["characters"][key])
            save_record(data_dir, make_record(uuid.uuid4().hex, context, slot, filename, data, "imported"))
    return filename


def save_generated(data_dir, plan, source):
    validate(plan, schema("generation-plan"))
    validate_context(plan)
    data = image_bytes(source)
    with locked(data_dir):
        migrate_catalog_locked(data_dir, load_config(data_dir))
        path = record_path(data_dir, plan["request_id"])
        if path.exists():
            previous = read_json(path)
            validate(previous, schema("image-record"))
            validate_context(previous)
            if (previous["id"] != plan["request_id"] or previous["image_sha256"] != digest(data) or previous["context_key"] != plan["context_key"]
                    or previous["expression"] != plan["expression"] or previous["origin"] != "image_gen"):
                raise Invalid("request id already saved with a different result")
            if digest(image_bytes(asset_path(data_dir, previous["asset"]))) != previous["image_sha256"]:
                raise Invalid("saved image is damaged; keep the new output and retry with a new plan")
            return previous
        context = {key: plan[key] for key in ("character_id", "character_fingerprint", "reference_sha256", "context_key", "reference_asset")}
        record = make_record(plan["request_id"], context, plan["expression"], write_asset(data_dir, data), data, "image_gen")
        # Keep the captured generation identity even if settings changed while the tool ran.
        save_record(data_dir, record)
        return record


def invalidate_image(data_dir, record_id):
    with locked(data_dir):
        path = record_path(data_dir, record_id)
        record = read_json(path)
        validate(record, schema("image-record"))
        record["usable"] = False
        save_record(data_dir, record)


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


def expression_plan(data_dir, resolved, mood, force=False):
    character = resolved["character"]
    context = image_context(data_dir, resolved["settings"]["character"], character)
    cached = cached_image(data_dir, context, mood) if not force else None
    if cached:
        return cached
    if context["reference_sha256"] in ("none", "missing"):
        return {"action": "unavailable", "expression": mood, "reason": "reference image is not registered"}
    return {"action": "generate", "request_id": uuid.uuid4().hex, **context, "expression": mood,
            "reference_path": str(asset_path(data_dir, context["reference_asset"])),
            "appearance_data": character["appearance"], "generator": "Codex image_gen"}


def set_plan(data_dir, resolved):
    migrate_catalog(data_dir)
    plans = [expression_plan(data_dir, resolved, mood) for mood in EXPRESSIONS]
    return {"action": "prepare_set", "expressions": list(EXPRESSIONS), "plans": plans,
            "complete": all(plan["action"] == "use_cached" for plan in plans)}


def visual_plan(data_dir, resolved, review):
    mood = expression(review)
    migrate_catalog(data_dir)
    mode = resolved["settings"]["image_mode"]
    if mode == "pre_generate":
        result = set_plan(data_dir, resolved)
        if not result["complete"]:
            return result | {"expression": mood}
    return expression_plan(data_dir, resolved, mood, force=mode == "always")


def main(argv=None):
    # Stable JSON encoding for Windows redirected output, including character names.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show")
    sub.add_parser("migrate")
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
    saved = sub.add_parser("save-generated")
    saved.add_argument("plan", type=Path)
    saved.add_argument("source", type=Path)
    rejected = sub.add_parser("invalidate-image")
    rejected.add_argument("record_id")
    for name in ("resolve", "plan", "plan-set"):
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
        elif args.command == "migrate":
            migrate_catalog(args.data_dir)
            result = {"migrated": True}
        elif args.command == "set":
            def change(doc):
                if args.key == "allow_project_overrides":
                    if args.value not in ("true", "false"):
                        raise Invalid("use true or false")
                    doc[args.key] = args.value == "true"
                else:
                    doc["settings"][args.key] = MODE_ALIASES.get(args.value, args.value) if args.key == "image_mode" else args.value
            update(args.data_dir, change)
            result = {"saved": True}
        elif args.command == "set-project":
            if args.path.name != ".persona-review.json":
                raise Invalid("project filename must be .persona-review.json")
            with locked(args.path.parent, ".persona-review.lock"):
                overrides = read_json(args.path) if args.path.exists() else {}
                validate(overrides, schema("settings"))
                overrides[args.key] = MODE_ALIASES.get(args.value, args.value) if args.key == "image_mode" else args.value
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
        elif args.command == "save-generated":
            result = save_generated(args.data_dir, read_json(args.plan), args.source)
        elif args.command == "invalidate-image":
            invalidate_image(args.data_dir, args.record_id)
            result = {"invalidated": args.record_id, "image_preserved": True}
        elif args.command == "validate-review":
            validate_review(read_json(args.review))
            result = {"valid": True}
        else:
            explicit = {key: getattr(args, key) for key in DEFAULTS if getattr(args, key) is not None}
            resolved = resolve(args.data_dir, args.project, explicit)
            if args.command == "resolve":
                result = resolved
            elif args.command == "plan-set":
                result = set_plan(args.data_dir, resolved)
            else:
                result = visual_plan(args.data_dir, resolved, read_json(args.review))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (Invalid, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"persona-review: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
