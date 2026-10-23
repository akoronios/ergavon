from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


def validate_greek_vat(value: str) -> None:
    """Validate a Greek ΑΦΜ by its check digit (GR-01.2).

    Accepts 9 digits, optionally prefixed with EL or GR.
    """
    if not value:
        return
    afm = value.strip().upper()
    if afm[:2] in ("EL", "GR"):
        afm = afm[2:]
    if len(afm) != 9 or not afm.isdigit() or afm == "000000000":
        raise ValidationError(_("ΑΦΜ must be 9 digits."), code="afm_format")
    total = sum(int(d) * (2 ** (8 - i)) for i, d in enumerate(afm[:8]))
    if (total % 11) % 10 != int(afm[8]):
        raise ValidationError(_("ΑΦΜ check digit is invalid."), code="afm_check_digit")
