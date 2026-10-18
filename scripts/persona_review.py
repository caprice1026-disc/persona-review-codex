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
DEFAULTS = {"character_id": "guide", "persona_id": "default", "image_mode": "hybrid", "scope": "auto", "focus": []}
SETTING_KEYS = (*DEFAULTS, "character", "review_mode")
GUIDE = {"name": "ガイド", "persona": "穏やかで簡潔な日本語。根拠を添えて話す。", "appearance": "", "images": {}}
GUIDE_PERSONA = {"name": GUIDE["name"], "style": GUIDE["persona"]}
GUIDE_V2 = {key: copy.deepcopy(value) for key, value in GUIDE.items() if key != "persona"} | {"persona_id": "guide"}
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
                 "enum", "pattern", "minLength", "maxLength", "minimum", "minItems", "maxItems"}
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
        if len(value) < rule.get("minItems", 0) or len(value) > rule.get("maxItems", float("inf")):
            raise Invalid(f"{location}: invalid item count")
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


def canonical_settings(value):
    validate(value, schema("settings"))
    result = copy.deepcopy(value)
    for alias, canonical in (("character", "character_id"), ("review_mode", "scope")):
        if alias in result:
            if canonical in result and result[canonical] != result[alias]:
                raise Invalid(f"conflicting settings: {alias} and {canonical}")
            result[canonical] = result.pop(alias)
    if "image_mode" in result:
        result["image_mode"] = MODE_ALIASES.get(result["image_mode"], result["image_mode"])
    if "focus" in result and len(set(result["focus"])) != len(result["focus"]):
        raise Invalid("focus entries must be unique")
    return result


def persona_id(value):
    result = character_id(value)
    if result == "default":
        raise Invalid("default is reserved for the character's default persona")
    return result


def legacy_persona_id(key):
    return "legacy-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def current_config(doc):
    """Return a detached v2 view; reading v1 never changes disk."""
    result = copy.deepcopy(doc)
    result["settings"] = canonical_settings(result["settings"])
    if result["version"] == 1:
        result["version"] = 2
        result["personas"] = {"guide": copy.deepcopy(GUIDE_PERSONA)}
        for key, character in result["characters"].items():
            identifier = legacy_persona_id(key)
            result["personas"][identifier] = {"name": character["name"], "style": character.pop("persona")}
            character["persona_id"] = identifier
    return result


def validate_config(doc):
    if type(doc) is not dict or type(doc.get("version")) is not int or doc["version"] not in (1, 2):
        raise Invalid("invalid config version")
    validate(doc, schema("config" if doc["version"] == 1 else "config-v2"))
    settings = canonical_settings(doc["settings"])
    for key, value in doc["characters"].items():
        character_id(key)
        validate(value, schema("character" if doc["version"] == 1 else "character-v2"))
    current = current_config(doc)
    for key, value in current["personas"].items():
        persona_id(key)
        validate(value, schema("persona"))
    for value in current["characters"].values():
        if value["persona_id"] not in current["personas"] and value["persona_id"] != "guide":
            raise Invalid("character persona is not registered")
    selected = settings.get("character_id", "guide")
    if selected != "guide" and selected not in current["characters"]:
        raise Invalid("default character is not registered")
    selected = settings.get("persona_id", "default")
    if selected not in ("default", "guide") and selected not in current["personas"]:
        raise Invalid("default persona is not registered")


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
    atomic_bytes(path, data)


def atomic_bytes(path, data):
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


def save_config(data_dir, original, doc):
    validate_config(doc)
    # Check the final size before creating a migration backup or replacing config.
    if len((json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")) > MAX_JSON_BYTES:
        raise Invalid(f"JSON exceeds {MAX_JSON_BYTES} bytes")
    path = Path(data_dir) / "config.json"
    if original["version"] == 1 and path.exists():
        data = path.read_bytes()
        backup = path.with_name("config.v1-backup-" + digest(data) + ".json")
        if backup.exists():
            if backup.read_bytes() != data:
                raise Invalid("migration backup does not match original config")
        else:
            atomic_bytes(backup, data)
    atomic_json(path, doc)


def update(data_dir, mutation):
    with locked(data_dir):
        original = load_config(data_dir)
        doc = current_config(original)
        mutation(doc)
        doc["settings"] = canonical_settings(doc["settings"])
        validate_config(doc)
        migrate_catalog_locked(data_dir, original)
        save_config(data_dir, original, doc)


def resolve(data_dir, project=None, explicit=None):
    doc = current_config(load_config(data_dir))
    settings = copy.deepcopy(DEFAULTS)
    sources = {key: "bundled" for key in DEFAULTS}
    ignored = bool(project and not doc["allow_project_overrides"])
    layers = [(doc["settings"], "global")]
    if project and doc["allow_project_overrides"]:
        layers.append((read_json(project), "project"))
    layers.append((explicit if explicit is not None else {}, "explicit"))
    for values, source in layers:
        values = canonical_settings(values)
        settings.update(values)
        sources.update({key: source for key in values})
    key = settings["character_id"]
    character = doc["characters"].get(key, GUIDE_V2 if key == "guide" else None)
    if character is None:
        raise Invalid(f"unregistered character: {key}")
    selected = settings["persona_id"]
    if selected == "default":
        selected = character["persona_id"]
        sources["persona_id"] = "character_default"
    persona = doc["personas"].get(selected, GUIDE_PERSONA if selected == "guide" else None)
    if persona is None:
        raise Invalid(f"unregistered persona: {selected}")
    settings["persona_id"] = selected
    # Compatibility projections for existing helper consumers.
    settings.update(character=key, review_mode=settings["scope"])
    sources.update(character=sources["character_id"], review_mode=sources["scope"])
    return {"settings": settings, "sources": sources, "project_ignored": ignored,
            "character": copy.deepcopy(character) | {"persona": persona["style"]},
            "persona": copy.deepcopy(persona)}


def setting_value(key, value):
    if key == "focus":
        return [] if not value else value.split(",")
    return MODE_ALIASES.get(value, value) if key == "image_mode" else value


def put_setting(settings, key, value):
    # Setting either spelling replaces its equivalent, preserving unrelated keys.
    aliases = {"character": "character_id", "character_id": "character",
               "review_mode": "scope", "scope": "review_mode"}
    if key in aliases:
        settings.pop(aliases[key], None)
    settings[key] = setting_value(key, value)
    canonical_settings(settings)


def legacy_persona_binding(doc, key, definition):
    """Legacy character edits never modify a registered/shared persona in place."""
    current = doc["characters"].get(key, {}).get("persona_id")
    existing = doc["personas"].get(current, GUIDE_PERSONA if current == "guide" else None)
    if existing == definition:
        return current
    stem = legacy_persona_id(key)
    candidate = stem
    suffix = 1
    while candidate in doc["personas"]:
        candidate = f"{stem}-{suffix}"
        suffix += 1
    doc["personas"][candidate] = definition
    return candidate


def register_character(data_dir, key, definition):
    character_id(key)
    if type(definition) is not dict:
        raise Invalid("character definition must be an object")
    legacy = "persona" in definition
    validate(definition, schema("character" if legacy else "character-v2"))
    if definition["images"]:
        raise Invalid("register requires empty images; use import-image")
    def change(doc):
        value = copy.deepcopy(definition)
        if legacy:
            persona = {"name": value["name"], "style": value.pop("persona")}
            value["persona_id"] = legacy_persona_binding(doc, key, persona)
        value["images"] = doc["characters"].get(key, {}).get("images", {})
        doc["characters"][key] = value
    update(data_dir, change)


def register_persona(data_dir, key, definition):
    persona_id(key)
    validate(definition, schema("persona"))
    update(data_dir, lambda doc: doc["personas"].update({key: copy.deepcopy(definition)}))


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
        original = load_config(data_dir)
        migrate_catalog_locked(data_dir, original)
        doc = current_config(original)
        if key not in doc["characters"]:
            if key != "guide":
                raise Invalid("register the character first")
            doc["characters"][key] = copy.deepcopy(GUIDE_V2)
        filename = write_asset(data_dir, data)
        if slot == "reference":
            doc["characters"][key]["images"][slot] = filename
            save_config(data_dir, original, doc)
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
    cached_only = resolved["settings"]["image_mode"] == "cached_only"
    cached = cached_image(data_dir, context, mood) if not force or cached_only else None
    if cached:
        return cached
    if cached_only:
        return {"action": "unavailable", "expression": mood,
                "reason": "cached_only: no usable saved image; image generation is disabled"}
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
    setting.add_argument("key", choices=[*SETTING_KEYS, "allow_project_overrides"])
    setting.add_argument("value")
    project_setting = sub.add_parser("set-project")
    project_setting.add_argument("path", type=Path)
    project_setting.add_argument("key", choices=list(SETTING_KEYS))
    project_setting.add_argument("value")
    register = sub.add_parser("register")
    register.add_argument("id")
    register.add_argument("definition", type=Path)
    persona = sub.add_parser("register-persona")
    persona.add_argument("id")
    persona.add_argument("definition", type=Path)
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
        for key in SETTING_KEYS:
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
            update(args.data_dir, lambda doc: None)
            result = {"migrated": True}
        elif args.command == "set":
            def change(doc):
                if args.key == "allow_project_overrides":
                    if args.value not in ("true", "false"):
                        raise Invalid("use true or false")
                    doc[args.key] = args.value == "true"
                else:
                    put_setting(doc["settings"], args.key, args.value)
            update(args.data_dir, change)
            result = {"saved": True}
        elif args.command == "set-project":
            if args.path.name != ".persona-review.json":
                raise Invalid("project filename must be .persona-review.json")
            with locked(args.path.parent, ".persona-review.lock"):
                overrides = read_json(args.path) if args.path.exists() else {}
                validate(overrides, schema("settings"))
                put_setting(overrides, args.key, args.value)
                resolve(args.data_dir, explicit=overrides)
                atomic_json(args.path, overrides)
            result = {"saved": True, "project_overrides_enabled": load_config(args.data_dir)["allow_project_overrides"]}
        elif args.command == "register":
            register_character(args.data_dir, args.id, read_json(args.definition))
            result = {"registered": args.id}
        elif args.command == "register-persona":
            register_persona(args.data_dir, args.id, read_json(args.definition))
            result = {"registered_persona": args.id}
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
            explicit = {key: setting_value(key, getattr(args, key)) for key in SETTING_KEYS if getattr(args, key) is not None}
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
