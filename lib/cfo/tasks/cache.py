"""Content-addressed cache of accepted task outputs, shared across runs."""
import os

from cfo.io import read_json, sha256_bytes, write_json_atomic


def cache_key(task, rules, prompt, schema, inputs):
    parts = [task.id.encode(), str(task.version).encode(), task.tier.encode(), rules, prompt, schema]
    for name in sorted(inputs):
        parts.extend([name.encode(), inputs[name]])
    return sha256_bytes(b"\x00".join(sha256_bytes(part).encode() for part in parts))


def _path(cache_dir, key):
    return os.path.join(cache_dir, key[:2], key + ".json")


def cache_get(cache_dir, key):
    return read_json(_path(cache_dir, key))


def cache_put(cache_dir, key, data):
    write_json_atomic(_path(cache_dir, key), data)
