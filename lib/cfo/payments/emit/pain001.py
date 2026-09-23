"""ISO 20022 pain.001.001.09 (Customer Credit Transfer Initiation) emitter.

Renders a `Batch` per task-4-brief.md's element structure: `GrpHdr` and
`PmtInf` each carry their own `NbOfTxs`/`CtrlSum`, and the two must agree --
a receiving bank checks exactly that agreement before it looks at a single
transaction, so both are computed from the same `batch.count` /
`batch.control_total` rather than from anything counted twice by hand.

**On schema validation.** Full XSD conformance is not verified here: that
needs `lxml`, which the toolkit does not depend on (see task-4-brief.md).
What this module does check, by construction, is the element paths, the
namespace, and the agreement of the two control sums -- the same things
the acceptance tests assert by parsing the output back with
`xml.etree.ElementTree`. Verifying the rest would mean fetching the
published pain.001.001.09 schema and validating against it with `lxml`.

Some elements the standard treats as optional are omitted rather than
written empty, because task-4-brief.md calls this out specifically for one
of them (`CdtrAgt` with no BIC) and the same reasoning -- an absent element
reads as "not supplied", an empty one reads as "supplied and blank" -- holds
for the others noted below. Elements the brief lists without that caveat
(`InitgPty/Nm`, `Dbtr/Nm`, `DbtrAgt/.../BICFI`, `RmtInf/Ustrd`) are always
written, blank if the caller gave nothing, so every acceptance-tested path
is always present.

`Batch`/`Payment` don't carry an initiating party, a debtor name or the
debtor's own BIC -- pain.001 wants them and the payment model doesn't (see
lib/cfo/payments/model.py's docstring on what does and doesn't belong
there) -- so they come in as keyword options, each with a blank-but-present
default rather than a required argument, so `emit(batch)` alone still
produces a structurally complete message.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from xml.etree import ElementTree as ET

from cfo.console import ToolkitError

NAMESPACE = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.09"
_TWO_PLACES = Decimal("0.01")
# Money is a Decimal all the way to the formatted string: quantised to two
# places (never float, never str(Decimal) unquantised -- see the module
# docstring and task-4-brief.md's 0.1 + 0.2 example) with ROUND_HALF_UP,
# the conventional choice for a payment amount over Decimal's default
# banker's rounding.
_ADDRESS_FIELDS = (("street", "StrtNm"), ("building", "BldgNb"), ("town", "TwnNm"),
                    ("post_code", "PstCd"), ("country", "Ctry"))


def _amount(value):
    return str(Decimal(value).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))


def _sub(parent, tag, text=None):
    element = ET.SubElement(parent, tag)
    if text is not None:
        element.text = text
    return element


def _address(parent, address):
    """`PstlAdr` -- only the keys `address` actually carries are written;
    pain.001 allows a partial postal address, and a batch built from a
    document a supplier sent is unlikely to have all five."""
    postal = _sub(parent, "PstlAdr")
    for key, tag in _ADDRESS_FIELDS:
        value = (address or {}).get(key)
        if value:
            _sub(postal, tag, str(value))


def emit(batch, *, msg_id=None, created_at=None, initiating_party_name="",
         debtor_name="", debtor_bic="") -> bytes:
    """`msg_id`/`created_at` default to a fresh id and `now()` so a caller
    never has to supply them just to get a valid message; pass both
    explicitly for a reproducible fixture (the acceptance tests do).

    `debtor_name` and `initiating_party_name` have no default, and the
    empty string is refused rather than accepted (see below).
    """
    # `Dbtr/Nm` and `InitgPty/Nm` are mandatory in pain.001 and are the only
    # required values this emitter cannot take from the batch. Letting them
    # default to "" wrote the parent element with no name inside it: a file
    # that looks complete, passes every structural check here, and is then
    # rejected by the bank that receives it. Refuse instead, and say which one
    # is missing, so a caller that forgot finds out here rather than from its
    # bank two days later.
    missing = [name for name, value in (("debtor_name", debtor_name),
                                        ("initiating_party_name", initiating_party_name))
               if not str(value).strip()]
    if missing:
        raise ToolkitError([(name, "pain.001 requires it; a file without it will be "
                                   "rejected by the bank") for name in missing])

    msg_id = msg_id or f"HEDJ-{uuid.uuid4()}"
    created_at = created_at or datetime.now(timezone.utc)
    control_sum = _amount(batch.control_total)
    n_txs = str(batch.count)

    # A bare `xmlns` attribute on the root, rather than ElementTree's own
    # `{uri}Tag` qualification on every element: every unprefixed descendant
    # is then in `NAMESPACE` by the ordinary rules of XML namespaces, which
    # is exactly what a parser (including the acceptance tests' own
    # `ET.fromstring`) will resolve back out.
    document = ET.Element("Document", {"xmlns": NAMESPACE})
    initn = _sub(document, "CstmrCdtTrfInitn")

    grp_hdr = _sub(initn, "GrpHdr")
    _sub(grp_hdr, "MsgId", msg_id)
    _sub(grp_hdr, "CreDtTm", created_at.strftime("%Y-%m-%dT%H:%M:%S"))
    _sub(grp_hdr, "NbOfTxs", n_txs)
    _sub(grp_hdr, "CtrlSum", control_sum)
    _sub(_sub(grp_hdr, "InitgPty"), "Nm", initiating_party_name)

    pmt_inf = _sub(initn, "PmtInf")
    _sub(pmt_inf, "PmtInfId", msg_id)
    _sub(pmt_inf, "PmtMtd", "TRF")
    _sub(pmt_inf, "BtchBookg", "true")
    _sub(pmt_inf, "NbOfTxs", n_txs)
    _sub(pmt_inf, "CtrlSum", control_sum)
    _sub(pmt_inf, "ReqdExctnDt", batch.execution_date.isoformat())
    _sub(_sub(pmt_inf, "Dbtr"), "Nm", debtor_name)
    _sub(_sub(_sub(pmt_inf, "DbtrAcct"), "Id"), "IBAN", batch.debtor_account)
    _sub(_sub(_sub(pmt_inf, "DbtrAgt"), "FinInstnId"), "BICFI", debtor_bic)

    for payment in batch.payments:
        tx = _sub(pmt_inf, "CdtTrfTxInf")
        _sub(_sub(tx, "PmtId"), "EndToEndId", payment.reference or str(uuid.uuid4()))

        instd_amt = _sub(_sub(tx, "Amt"), "InstdAmt", _amount(payment.amount))
        instd_amt.set("Ccy", payment.currency)

        # Omitted, not emitted empty, when there is no BIC: an absent
        # CdtrAgt reads as "route by IBAN/clearing code instead", an empty
        # one reads as "the beneficiary's bank has no identifier at all".
        if payment.bic:
            _sub(_sub(_sub(tx, "CdtrAgt"), "FinInstnId"), "BICFI", payment.bic)

        cdtr = _sub(tx, "Cdtr")
        _sub(cdtr, "Nm", payment.beneficiary_name)
        _address(cdtr, payment.beneficiary_address)

        cdtr_acct_id = _sub(_sub(tx, "CdtrAcct"), "Id")
        if payment.iban:
            _sub(cdtr_acct_id, "IBAN", payment.iban)
        else:
            _sub(_sub(cdtr_acct_id, "Othr"), "Id", payment.account_number)

        # `remittance` -- the free-text a supplier reads on their bank
        # statement -- falls back to `reference` when blank, but the two are
        # distinct fields now (task-3-brief.md's ruling): `EndToEndId` above
        # is `payment.reference` alone, untouched, because that is the join
        # key `exception-meta.json`, `dropped_refs` and the instructed-lines
        # digest all rely on. A `--remittance` amendment must never be able
        # to change what EndToEndId says, or it silently breaks the link
        # that stops a rejected/excluded payment being paid -- the same
        # defect (a rejected payment going out anyway) in a new costume.
        _sub(_sub(tx, "RmtInf"), "Ustrd", payment.remittance or payment.reference)

    return ET.tostring(document, encoding="utf-8", xml_declaration=True)
