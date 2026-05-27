"""
validator.py
------------
Validates extracted FNOL fields for correctness and internal consistency.

What this module does:
  1. Format checks  – are dates, phone numbers, VINs in the right format?
  2. Logic checks   – does estimated_damage match initial_estimate?
                      is the incident_date within the policy effective period?
  3. Suspicious data detection – flags values that look anomalous

Returns a list of ValidationIssue objects so the caller can decide
what to do (log warnings, route to Manual Review, etc.).
"""

import re
from dataclasses import dataclass
from datetime import datetime
from utils import logger


# ──────────────────────────────────────────────
# DATA CLASSES
# ──────────────────────────────────────────────

@dataclass
class ValidationIssue:
    """Represents a single validation problem found in an extracted field."""
    field:    str   # Which field has the issue
    severity: str   # "WARNING" or "ERROR"
    message:  str   # Human-readable description of the problem

    def __str__(self):
        return f"[{self.severity}] {self.field}: {self.message}"


# ──────────────────────────────────────────────
# MAIN VALIDATION FUNCTION
# ──────────────────────────────────────────────

def validate_fields(extracted: dict) -> list[ValidationIssue]:
    """
    Run all validation checks on the extracted fields dictionary.

    Args:
        extracted: The dict returned by extractor.extract_fields()

    Returns:
        A list of ValidationIssue objects (empty list = all checks passed).
    """
    issues = []

    issues += _check_date_format("incident_date",   extracted.get("incident_date"))
    issues += _check_date_format("effective_dates",  extracted.get("effective_dates"), allow_range=True)
    issues += _check_phone_or_email(extracted.get("contact_details"))
    issues += _check_vin(extracted.get("asset_id"))
    issues += _check_damage_consistency(
        extracted.get("estimated_damage"),
        extracted.get("initial_estimate")
    )
    issues += _check_incident_within_policy(
        extracted.get("incident_date"),
        extracted.get("effective_dates")
    )
    issues += _check_suspicious_description(extracted.get("incident_description"))
    issues += _check_damage_amount(extracted.get("estimated_damage"))

    # Log a summary
    errors   = [i for i in issues if i.severity == "ERROR"]
    warnings = [i for i in issues if i.severity == "WARNING"]
    logger.info(f"Validation complete — {len(errors)} error(s), {len(warnings)} warning(s)")

    for issue in issues:
        if issue.severity == "ERROR":
            logger.error(str(issue))
        else:
            logger.warning(str(issue))

    return issues


# ──────────────────────────────────────────────
# INDIVIDUAL CHECKS
# ──────────────────────────────────────────────

def _check_date_format(field_name: str, value: str | None, allow_range: bool = False) \
        -> list[ValidationIssue]:
    """Check that a date (or date range) matches common MM/DD/YYYY formats."""
    if not value:
        return []  # Missing field is reported by extractor, not validator

    # Date range pattern: "MM/DD/YYYY to MM/DD/YYYY" or with dashes
    date_pattern = r"\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}"

    if allow_range:
        # Accept either a single date OR a range
        valid = re.search(date_pattern, value)
    else:
        valid = re.fullmatch(r"\s*" + date_pattern + r"\s*", value.strip())

    if not valid:
        return [ValidationIssue(
            field=field_name,
            severity="WARNING",
            message=f"Date value '{value}' does not match expected MM/DD/YYYY format."
        )]
    return []


def _check_phone_or_email(value: str | None) -> list[ValidationIssue]:
    """Check that contact_details looks like a valid phone number or email."""
    if not value:
        return []

    phone_ok = re.search(r"[\d\(\)\+][\d\s\(\)\-\.]{6,}", value)
    email_ok = re.search(r"[\w\.\+\-]+@[\w\.\-]+\.\w{2,6}", value)

    if not phone_ok and not email_ok:
        return [ValidationIssue(
            field="contact_details",
            severity="WARNING",
            message=f"Contact value '{value}' does not look like a phone number or email address."
        )]
    return []


def _check_vin(value: str | None) -> list[ValidationIssue]:
    """
    Validate a Vehicle Identification Number (VIN).
    A proper VIN is exactly 17 characters, no I, O, or Q.
    We allow shorter strings (plate numbers) with a lower-severity warning.
    """
    if not value:
        return []

    # Remove spaces before checking
    v = value.replace(" ", "").upper()

    if len(v) == 17:
        invalid_chars = set(re.findall(r"[IOQ]", v))
        if invalid_chars:
            return [ValidationIssue(
                field="asset_id",
                severity="WARNING",
                message=f"VIN '{value}' contains invalid characters: {invalid_chars}. "
                        "Valid VINs never use I, O, or Q."
            )]
        return []  # Valid 17-char VIN
    elif 3 <= len(v) < 17:
        # Likely a plate number – acceptable
        return []
    else:
        return [ValidationIssue(
            field="asset_id",
            severity="WARNING",
            message=f"Asset ID '{value}' is {len(v)} characters — "
                    "VINs should be exactly 17 characters."
        )]


def _check_damage_consistency(damage: str | None, estimate: str | None) \
        -> list[ValidationIssue]:
    """
    Flag if estimated_damage and initial_estimate differ by more than 10%.
    This could indicate a data entry error or intentional mismatch.
    """
    if not damage or not estimate:
        return []

    d = _parse_amount(damage)
    e = _parse_amount(estimate)

    if d is None or e is None:
        return []

    if d == 0 and e == 0:
        return []

    # Percentage difference
    larger  = max(d, e)
    smaller = min(d, e)
    diff_pct = ((larger - smaller) / larger) * 100

    if diff_pct > 10:
        return [ValidationIssue(
            field="estimated_damage / initial_estimate",
            severity="WARNING",
            message=f"Estimated damage (${d:,.2f}) and initial estimate (${e:,.2f}) "
                    f"differ by {diff_pct:.1f}%. This may warrant review."
        )]
    return []


def _check_incident_within_policy(incident_date: str | None, effective_dates: str | None) \
        -> list[ValidationIssue]:
    """
    Check that the incident date falls within the policy effective period.
    Only runs if both values are present and parseable.
    """
    if not incident_date or not effective_dates:
        return []

    # Try to extract start and end dates from the effective_dates range
    date_re = r"(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})"
    matches = re.findall(date_re, effective_dates)

    inc = _parse_date(incident_date)
    if inc is None:
        return []

    if len(matches) == 2:
        start = _parse_date(matches[0])
        end   = _parse_date(matches[1])
        if start and end:
            if not (start <= inc <= end):
                return [ValidationIssue(
                    field="incident_date",
                    severity="ERROR",
                    message=f"Incident date {incident_date} falls outside the policy "
                            f"effective period ({matches[0]} – {matches[1]}). "
                            "Coverage may not apply."
                )]
    return []


def _check_suspicious_description(description: str | None) -> list[ValidationIssue]:
    """
    Look for words in the incident description that commonly appear
    in fraudulent or problematic claims.
    """
    if not description:
        return []

    suspicious_words = [
        "fraud", "fraudulent", "staged", "inconsistent", "suspicious",
        "fabricated", "false", "misrepresent", "exaggerated"
    ]

    found = [w for w in suspicious_words
             if re.search(r"\b" + w + r"\b", description, re.IGNORECASE)]

    if found:
        return [ValidationIssue(
            field="incident_description",
            severity="ERROR",
            message=f"Description contains suspicious keyword(s): {found}. "
                    "Recommend referral to SIU."
        )]
    return []


def _check_damage_amount(damage: str | None) -> list[ValidationIssue]:
    """Basic sanity check: damage amounts above $500,000 may be entry errors."""
    if not damage:
        return []

    amount = _parse_amount(damage)
    if amount is None:
        return [ValidationIssue(
            field="estimated_damage",
            severity="WARNING",
            message=f"Could not parse '{damage}' as a numeric amount."
        )]

    if amount > 500_000:
        return [ValidationIssue(
            field="estimated_damage",
            severity="WARNING",
            message=f"Damage amount ${amount:,.2f} exceeds $500,000. "
                    "Please verify this is not a data entry error."
        )]
    return []


# ──────────────────────────────────────────────
# HELPER FUNCTIONS
# ──────────────────────────────────────────────

def _parse_amount(value: str) -> float | None:
    """Convert a string like '$18,500' or '18500.00' to a float."""
    cleaned = re.sub(r"[,\$\s]", "", value)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_date(value: str) -> datetime | None:
    """Try to parse a date string into a datetime object."""
    formats = ["%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%m-%d-%y"]
    for fmt in formats:
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def issues_to_dict_list(issues: list[ValidationIssue]) -> list[dict]:
    """Convert validation issues to a list of plain dicts (for JSON serialisation)."""
    return [{"field": i.field, "severity": i.severity, "message": i.message}
            for i in issues]
