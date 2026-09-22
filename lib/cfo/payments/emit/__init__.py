"""Emitter registry: one validated `Batch`, three renderings of it.

Each emitter is `emit(batch, **options) -> bytes | str`; the options differ
by emitter (`csv.emit` takes none, `pain001.emit` takes the message-header
fields the payment model doesn't carry) because each format needs
different things, not because this package imposes a shared signature
beyond `batch` as the first argument.

**None of them validates.** T3's `validate_batch` (`cfo.payments.model`)
owns every rule; an emitter given an invalid batch may produce nonsense,
and that is the caller's error to have prevented, not this package's to
re-check -- re-checking here is exactly how two versions of one rule end
up disagreeing with each other.

`EMITTERS` lets a caller select by name, so another emitter can be added
later without any caller needing to change.

One emitter is built for the hosted platform rather than for this plugin,
and is not distributed with it. It registers itself when present and is
simply absent when it is not, so the public build offers pain.001 and CSV
-- which is what a company's own bank accepts -- and the hosted build
offers all three. A caller should read `EMITTERS` rather than assume a
fixed set.
"""
import importlib
import pkgutil

from cfo.payments.emit import csv_emit, pain001

EMITTERS = {
    "pain001": pain001.emit,
    "csv": csv_emit.emit,
}

# Any further emitter in this package registers itself, under the `NAME` it
# declares. Discovered rather than imported by name: writing the module's name
# here would put it in the public plugin, which is the one thing leaving the
# module out is meant to avoid. A build without it simply finds nothing extra.
for _found in pkgutil.iter_modules(__path__):
    if _found.name.startswith("_") or _found.name in EMITTERS or _found.name in ("csv_emit", "pain001"):
        continue
    _module = importlib.import_module(f"{__name__}.{_found.name}")
    _name, _handler = getattr(_module, "NAME", None), getattr(_module, "emit", None)
    if _name and callable(_handler):
        EMITTERS[_name] = _handler
