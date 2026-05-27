"""
router.py
---------
Applies business routing rules to decide which queue a claim goes to.

Rules are evaluated in priority order (highest priority first):

  Priority 1 – INVESTIGATION FLAG
      Description contains fraud/suspicious keywords.

  Priority 2 – MANUAL REVIEW
      One or more mandatory fields are missing.

  Priority 3 – SPECIALIST QUEUE
      Claim type is "injury" or description mentions bodily harm.

  Priority 4 – FAST-TRACK
      All fields present, no flags, estimated damage < $25,000.

  Priority 5 – STANDARD REVIEW (default)
      All fields present, no flags, damage ≥ $25,000 or unparseable.
"""

import re
from utils import logger


# ──────────────────────────────────────────────
# KEYWORD LISTS
# ──────────────────────────────────────────────

# Words in the incident description that trigger an Investigation Flag
FRAUD_KEYWORDS = [
    "fraud", "fraudulent", "inconsistent", "staged", "suspicious",
    "fabricated", "false claim", "misrepresent", "exaggerated", "siu"
]

# Words that indicate a bodily injury claim type
INJURY_KEYWORDS = [
    "injury", "bodily injury", "personal injury",
    "medical", "injured", "hurt", "casualty", "death", "fatality"
]

# Routing queue names (constants to avoid typos)
ROUTE_INVESTIGATION = "Investigation Flag"
ROUTE_MANUAL        = "Manual Review"
ROUTE_SPECIALIST    = "Specialist Queue"
ROUTE_FAST_TRACK    = "Fast-track"
ROUTE_STANDARD      = "Standard Review"

# Damage threshold that separates Fast-track from Standard Review
FAST_TRACK_THRESHOLD = 25_000.00


# ──────────────────────────────────────────────
# MAIN ROUTING FUNCTION
# ──────────────────────────────────────────────

def determine_route(
    extracted_fields: dict,
    missing_fields:   list,
) -> tuple[str, str]:
    """
    Evaluate routing rules and return the appropriate queue name plus reasoning.

    Args:
        extracted_fields : dict from extractor.extract_fields()
        missing_fields   : list from extractor.extract_fields()

    Returns:
        (route_name, reasoning_string)
    """
    description  = extracted_fields.get("incident_description") or ""
    claim_type   = extracted_fields.get("claim_type") or ""
    damage_raw   = extracted_fields.get("estimated_damage") or ""
    estimate_raw = extracted_fields.get("initial_estimate") or ""

    # ── Rule 1: Fraud keywords ───────────────────────────────────────────
    fraud_hit = _find_keyword(description, FRAUD_KEYWORDS)
    if fraud_hit:
        reasoning = (
            f"The incident description contains the suspicious keyword '{fraud_hit}'. "
            "This claim has been flagged for review by the Special Investigations Unit (SIU) "
            "before any payment is authorised."
        )
        logger.info(f"Route → {ROUTE_INVESTIGATION} (fraud keyword: '{fraud_hit}')")
        return ROUTE_INVESTIGATION, reasoning

    # ── Rule 2: Missing mandatory fields ─────────────────────────────────
    if missing_fields:
        fields_str = ", ".join(missing_fields)
        reasoning = (
            f"The following mandatory fields could not be extracted from the document: "
            f"[{fields_str}]. A human adjuster must complete the record before the claim "
            "can be routed automatically."
        )
        logger.info(f"Route → {ROUTE_MANUAL} (missing: {fields_str})")
        return ROUTE_MANUAL, reasoning

    # ── Rule 3: Injury claim ─────────────────────────────────────────────
    injury_in_type = _find_keyword(claim_type, INJURY_KEYWORDS)
    injury_in_desc = _find_keyword(description, INJURY_KEYWORDS)

    if injury_in_type or injury_in_desc:
        hit = injury_in_type or injury_in_desc
        reasoning = (
            f"The claim type or incident description indicates bodily injury "
            f"(keyword matched: '{hit}'). This claim requires specialist assessment "
            "for medical and/or legal evaluation."
        )
        logger.info(f"Route → {ROUTE_SPECIALIST} (injury keyword: '{hit}')")
        return ROUTE_SPECIALIST, reasoning

    # ── Rules 4 & 5: Damage threshold ───────────────────────────────────
    # Use estimated_damage; fall back to initial_estimate if needed
    damage_value = _parse_amount(damage_raw) or _parse_amount(estimate_raw)

    if damage_value is not None:
        if damage_value < FAST_TRACK_THRESHOLD:
            reasoning = (
                f"All mandatory fields are present. No fraud keywords or injury indicators found. "
                f"Estimated damage ${damage_value:,.2f} is below the "
                f"${FAST_TRACK_THRESHOLD:,.0f} threshold — eligible for fast-track processing."
            )
            logger.info(f"Route → {ROUTE_FAST_TRACK} (damage: ${damage_value:,.2f})")
            return ROUTE_FAST_TRACK, reasoning
        else:
            reasoning = (
                f"All mandatory fields are present. No fraud keywords or injury indicators found. "
                f"Estimated damage ${damage_value:,.2f} meets or exceeds the "
                f"${FAST_TRACK_THRESHOLD:,.0f} threshold — routed to standard adjuster review."
            )
            logger.info(f"Route → {ROUTE_STANDARD} (damage: ${damage_value:,.2f})")
            return ROUTE_STANDARD, reasoning

    # ── Default fallback ─────────────────────────────────────────────────
    reasoning = (
        "All mandatory fields are present and no fraud keywords were detected, "
        "but the damage amount could not be parsed as a number. "
        "Defaulting to Standard Review for manual damage assessment."
    )
    logger.info(f"Route → {ROUTE_STANDARD} (damage not parseable)")
    return ROUTE_STANDARD, reasoning


# ──────────────────────────────────────────────
# HELPER FUNCTIONS
# ──────────────────────────────────────────────

def _find_keyword(text: str, keywords: list) -> str | None:
    """
    Search for any keyword from the list in the given text.
    Uses word-boundary matching so 'staging' won't trigger 'staged'.
    Returns the matched keyword string, or None.
    """
    text_lower = text.lower()
    for kw in keywords:
        if re.search(r"\b" + re.escape(kw) + r"\b", text_lower):
            return kw
    return None


def _parse_amount(value: str) -> float | None:
    """Convert '18,500' or '$18500.00' to a float. Returns None on failure."""
    if not value:
        return None
    cleaned = re.sub(r"[,\$\s]", "", value)
    try:
        return float(cleaned)
    except ValueError:
        return None
