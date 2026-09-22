"""The payment model every emitter renders: pain.001 XML and CSV for the
customer's own bank, and a JSON payload for the payment provider the
`POST /mass-payments`. Validation lives here, once, so a new emitter
inherits every check instead of repeating it.

Four fields look redundant today and are not, and must not be "tidied
away" by someone who only sees the first two emitters:

- `beneficiary_address_flat` (one free-text address line) sits beside the
  structured `beneficiary_address` (`{"street", "building", "town",
  "post_code", "country"}`). pain.001 wants the structured form and has no
  field for the flat one; the provider's mass-payment API takes one flat string
  and has no field for the structured one. Both emitters read the same
  `Payment`, so both shapes of the same address have to live here even
  though, today, only the structured one is used.
- `purpose_of_payment`, `reason_for_trade` and `bank_code` are used by none
  of pain.001's fields and required by the provider's. The provider confirmed (question
  T-19, 25 Aug 2026) that they publish no corridor code list for
  `purpose_of_payment`, so that reference data is ours to hold -- it is not
  something a later emitter could derive from a field already here.

Drop any of the five fields above (the four *kinds* of apparent
redundancy: the flat/structured address pair, `purpose_of_payment`,
`reason_for_trade`, `bank_code`) and the provider emitter cannot be written
without reopening this model, and everything already built on it.

Money is `Decimal` throughout, never `float`: a payment instruction moves
an exact amount, and `float` carries binary rounding error a bank should
never see. `validate_batch` treats a `float` amount as an error rather
than something to coerce, on purpose -- see `_validate_payment`.

`valid_iban` is injected into `validate_batch` rather than imported at
module scope, because `cfo.treasury.iban` (T1) is built in parallel with
this module. When the caller leaves it as `None` (the default), the real
function is imported lazily, and only once a payment actually has an IBAN
to check -- a batch with no IBANs at all never needs `cfo.treasury.iban`
to exist.

Splitting an oversized batch is not this module's job: a later task
(T9) hands `validate_batch` batches already within `max_payments`. This
module only rejects a batch that is still too big; it never fixes one.
"""
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# Configuration, never a constant at a call site: pass a different value to
# `validate_batch` for a bank whose own cap differs from the usual pain.001
# batch limit, rather than hard-coding 199 wherever a batch is checked.
MAX_PAYMENTS_PER_BATCH = 199

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
# beneficiary_address_flat, beneficiary_address, beneficiary_country and the
# other provider-only fields are deliberately not in this list -- see the
# module docstring. Only these four are required for every payment,
# regardless of which emitter eventually renders it.
_REQUIRED_FIELDS = ("beneficiary_name", "currency", "amount", "value_date")


@dataclass
class Payment:
    beneficiary_name: str
    beneficiary_address_flat: str        # one free-text line -- the provider's shape
    beneficiary_address: dict            # {"street", "building", "town",
                                          #  "post_code", "country"} -- pain.001's shape
    beneficiary_country: str             # ISO 3166-1 alpha-2
    iban: str = ""
    account_number: str = ""
    bic: str = ""
    bank_code: str = ""                  # local clearing code, scheme unknown
    bank_name: str = ""
    bank_address: str = ""
    bank_country: str = ""
    currency: str = ""                   # ISO 4217
    amount: Decimal = Decimal("0")
    value_date: date | None = None
    reference: str = ""
    purpose_of_payment: str = ""
    reason_for_trade: str = ""
    direction: str = "sell"
    trade_type: str = "spot"


@dataclass
class Batch:
    debtor_account: str
    execution_date: date
    sell_currency: str
    external_reference: str
    payments: list

    @property
    def control_total(self) -> Decimal:
        """The sum of the payments' amounts, in `Decimal` -- never `float`,
        so three awkward amounts (0.1, 0.2, 0.3) sum to exactly 0.6 rather
        than to something with a trailing rounding error."""
        return sum((payment.amount for payment in self.payments), Decimal("0"))

    @property
    def count(self) -> int:
        return len(self.payments)


def _missing(payment, field):
    """True when a required field was never really given a value: a blank
    or whitespace-only string, a bare `None` -- or, for `amount`, the
    dataclass's own zero default, which stands in for "not supplied" since
    a `Decimal` amount has no other value that could mean "empty"."""
    value = getattr(payment, field)
    if field == "amount":
        return value is None or (isinstance(value, Decimal) and value == 0)
    if field == "value_date":
        return value is None
    return not str(value or "").strip()


def validate_batch(batch, *, valid_iban=None, max_payments=MAX_PAYMENTS_PER_BATCH):
    """[(where, message)] -- every rule this model enforces, across the
    whole batch. An empty list means the batch is valid; nothing here
    raises `ToolkitError` itself (see the module docstring on where
    splitting and other batch-shaping jobs belong instead) -- a caller
    that wants to fail hard wraps a non-empty result in one.

    `valid_iban` defaults to `cfo.treasury.iban.valid_iban`, imported
    lazily the first time a payment actually has an IBAN to check (see the
    module docstring). Pass a fake to test the IBAN branch without needing
    a real bad IBAN.
    """
    errors = []
    check_iban = valid_iban

    for index, payment in enumerate(batch.payments):
        where = f"payments[{index}]"

        for field in _REQUIRED_FIELDS:
            if _missing(payment, field):
                errors.append((where, f"{field} is required"))

        amount = payment.amount
        if not _missing(payment, "amount"):
            if not isinstance(amount, Decimal):
                errors.append((where, f"amount must be a Decimal, not {type(amount).__name__}: "
                                      "money must not carry binary rounding error"))
            elif amount <= 0:
                errors.append((where, "amount must be a positive Decimal"))

        if payment.currency and not _CURRENCY_RE.match(payment.currency):
            errors.append((where, "currency must be an ISO 4217 three-letter code"))
        if payment.beneficiary_country and not _COUNTRY_RE.match(payment.beneficiary_country):
            errors.append((where, "beneficiary_country must be an ISO 3166-1 alpha-2 code"))

        # Either a passing IBAN, or an account number with something to route
        # it by. Neither being true is one error naming the payment, not a
        # separate complaint about the IBAN and another about the account.
        has_valid_iban = False
        if payment.iban:
            if check_iban is None:
                from cfo.treasury.iban import valid_iban as check_iban
            has_valid_iban = bool(check_iban(payment.iban))
        has_account = bool(payment.account_number) and bool(payment.bank_code or payment.bic)
        if not (has_valid_iban or has_account):
            errors.append((where, "needs either a valid IBAN, or an account number with a bank "
                                  "code or a BIC"))

        if not _missing(payment, "value_date") and payment.value_date < batch.execution_date:
            errors.append((f"{where}.value_date",
                           f"value_date {payment.value_date.isoformat()} is before the batch's "
                           f"execution_date {batch.execution_date.isoformat()}"))

    if batch.count > max_payments:
        errors.append(("payments", f"a batch can have at most {max_payments} payments; this "
                                   f"one has {batch.count} -- split it before calling "
                                   "validate_batch, this does not split it for you"))

    return errors
