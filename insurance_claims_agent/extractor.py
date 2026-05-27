"""
extractor.py
------------
Extracts structured FNOL fields from raw document text using regex patterns.

Design philosophy:
  - Each field has a primary pattern and one or more fallback patterns.
  - Patterns are tried in order; the first match wins.
  - A confidence score (0.0 – 1.0) is assigned to every field:
      1.0  → matched by the most specific / primary pattern
      0.75 → matched by a secondary/fallback pattern
      0.0  → field not found
  - The extractor never raises exceptions — missing fields simply get None.

Returned structure:
  extracted_fields : dict  – field_name → extracted string value (or None)
  confidence_scores: dict  – field_name → float confidence score
  missing_fields   : list  – names of mandatory fields with None values
"""

import re
from utils import logger


# ──────────────────────────────────────────────
# MANDATORY FIELD LIST
# ──────────────────────────────────────────────
# Every field in this list must be present for the claim to be auto-routed.
MANDATORY_FIELDS = [
    "policy_number",
    "policyholder_name",
    "effective_dates",
    "incident_date",
    "incident_time",
    "incident_location",
    "incident_description",
    "claimant_name",
    "contact_details",
    "asset_type",
    "asset_id",
    "estimated_damage",
    "claim_type",
    "initial_estimate",
]


# ──────────────────────────────────────────────
# PATTERN DEFINITIONS
# ──────────────────────────────────────────────
# Format:  field_name → list of (pattern_string, confidence_score)
# Patterns are tried top-to-bottom; the first match wins.
# Group 1 ( ) should capture the actual value.

FIELD_PATTERNS = {

    # ---- Policy Information ----

    "policy_number": [
        (r"policy\s*(?:number|no\.?|#)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-]{3,19})", 1.0),
        (r"pol(?:icy)?\s*[#:]?\s*([A-Z0-9\-]{5,20})", 0.75),
    ],

    "policyholder_name": [
        # Labelled variants – stop before common next-line labels
        (r"(?:name\s+of\s+insured|policyholder(?:'s)?\s*name|insured(?:'s)?\s*name)\s*[:\-]?\s*"
         r"([A-Za-z][A-Za-z\s\.\,\-]{2,50}?)(?=\s*(?:\n|date|dob|phone|address|fein|email))",
         1.0),
        (r"insured\s*[:\-]\s*([A-Za-z][A-Za-z\s\.]{2,40})", 0.75),
    ],

    "effective_dates": [
        # Range like 01/15/2024 to 01/15/2025
        (r"effective\s*(?:dates?)?\s*[:\-]?\s*"
         r"(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}\s*(?:to|[-–])\s*\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})",
         1.0),
        (r"policy\s*period\s*[:\-]?\s*"
         r"(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}.*?\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})",
         0.9),
        # Single effective date as fallback
        (r"effective\s*[:\-]?\s*(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})", 0.6),
    ],

    # ---- Incident Information ----

    "incident_date": [
        (r"(?:date\s*of\s*(?:loss|incident|accident)|incident\s*date|loss\s*date)\s*[:\-]?\s*"
         r"(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})",
         1.0),
        (r"(?:date\s+of\s+loss\s+and\s+time|date\s+filed)\s*[:\-]?\s*"
         r"(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})",
         0.9),
        (r"(?:accident|loss)\s*(?:on)?\s*[:\-]?\s*(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})", 0.75),
    ],

    "incident_time": [
        (r"(?:time\s*of\s*(?:loss|incident|accident)|incident\s*time)\s*[:\-]?\s*"
         r"(\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?)",
         1.0),
        (r"(?:at|@)\s*(\d{1,2}:\d{2}\s*(?:AM|PM|am|pm))", 0.85),
        (r"time\s*[:\-]?\s*(\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?)", 0.7),
    ],

    "incident_location": [
        (r"(?:location\s*of\s*(?:loss|incident|accident)|incident\s*location|"
         r"loss\s*location|place\s*of\s*(?:loss|accident))\s*[:\-]?\s*([^\n]{5,100})",
         1.0),
        (r"(?:occurred|happened)\s*(?:at|on|near)\s*[:\-]?\s*([^\n]{5,80})", 0.8),
        (r"(?:address\s*of\s*(?:loss|accident)|street)\s*[:\-]?\s*([^\n]{5,80})", 0.7),
    ],

    "incident_description": [
        # Capture the full multi-line block after the "Description of Accident:" label.
        # Stop when we reach a blank line followed by an ALL-CAPS section header.
        (r"(?:description\s*of\s*(?:accident|incident|loss)|"
         r"incident\s*description|accident\s*description)\s*[:\-]?\s*"
         r"(.*?)(?=\n\s*\n\s*(?:INVOLVED|ASSET|DAMAGE|CLAIM|POLICE|NOTES|ADJUSTER))",
         1.0),
        # Fallback: grab everything after the label until the next blank line
        (r"(?:description\s*of\s*(?:accident|incident|loss)|"
         r"incident\s*description|accident\s*description)\s*[:\-]?\s*"
         r"([^\n]+(?:\n[ \t]+[^\n]+){0,10})",
         0.85),
        (r"(?:describe|details|what\s*happened|narrative)\s*[:\-]?\s*([^\n]{10,})", 0.75),
    ],

    # ---- Involved Parties ----

    "claimant_name": [
        (r"(?:claimant(?:'s)?\s*name|name\s*of\s*claimant)\s*[:\-]?\s*"
         r"([A-Za-z][A-Za-z\s\.\,\-]{2,50}?)(?=\s*(?:\n|phone|address|dob|date|third))",
         1.0),
        (r"claimant\s*[:\-]\s*([A-Za-z][A-Za-z\s\.]{2,40})", 0.75),
    ],

    "third_parties": [
        (r"(?:third\s*part(?:y|ies)|other\s*(?:party|parties|driver|vehicle\s*owner))\s*[:\-]?\s*"
         r"([^\n]{3,100})",
         1.0),
        (r"(?:other\s*involved|witnesses?|passengers?)\s*[:\-]?\s*([^\n]{3,80})", 0.7),
    ],

    "contact_details": [
        # Phone number
        (r"(?:phone|contact\s*(?:number|phone|details?)|tel(?:ephone)?|mobile|cell)\s*[:\-]?\s*"
         r"([\+\(\)\d\s\-\.]{7,20})",
         1.0),
        # Email as fallback
        (r"(?:email|e-mail)\s*[:\-]?\s*([\w\.\+\-]+@[\w\.\-]+\.\w{2,6})", 0.8),
    ],

    # ---- Asset Details ----

    "asset_type": [
        (r"(?:asset\s*type|type\s*of\s*(?:vehicle|asset|property)|vehicle\s*type|body\s*type)\s*[:\-]?\s*"
         r"([A-Za-z][A-Za-z\s\/\-]{2,40}?)(?=\s*(?:\n|year|make|vin|plate|id))",
         1.0),
        # Grab make/model as proxy for asset type
        (r"(?:make|model|vehicle)\s*[:\-]?\s*([A-Za-z][A-Za-z\s\-]{1,30})", 0.65),
    ],

    "asset_id": [
        # Full 17-char VIN
        (r"(?:v\.?i\.?n\.?|vehicle\s*identification\s*(?:number)?|vin\s*(?:number)?)\s*[:\-]?\s*"
         r"([A-HJ-NPR-Z0-9]{17})",
         1.0),
        # Partial VIN or plate number as fallback
        (r"(?:plate\s*(?:number|no\.?)|license\s*plate|registration)\s*[:\-]?\s*"
         r"([A-Z0-9\-\s]{3,15})",
         0.75),
        (r"(?:asset\s*id|vin)\s*[:\-]\s*([A-HJ-NPR-Z0-9]{8,17})", 0.7),
    ],

    "estimated_damage": [
        (r"(?:estimated?\s*(?:damage|loss|amount|repair\s*cost)|damage\s*estimate|"
         r"estimate\s*amount)\s*[:\-]?\s*\$?\s*([\d,]+(?:\.\d{2})?)",
         1.0),
        (r"(?:repair\s*estimate|damage\s*amount|loss\s*amount)\s*[:\-]?\s*\$?\s*"
         r"([\d,]+(?:\.\d{2})?)",
         0.8),
        # Bare dollar amount – lower confidence
        (r"\$\s*([\d,]{3,}(?:\.\d{2})?)", 0.5),
    ],

    # ---- Other Mandatory Fields ----

    "claim_type": [
        (r"(?:claim\s*type|type\s*of\s*claim|nature\s*of\s*claim|loss\s*type|"
         r"line\s*of\s*business)\s*[:\-]?\s*([A-Za-z][A-Za-z\s\/\-]{2,50}?)(?=\s*(?:\n|policy|carrier|initial))",
         1.0),
        # Named loss types if no label found
        (r"\b(collision|comprehensive|liability|bodily\s*injury|personal\s*injury|"
         r"theft|property\s*damage|fire|flood|weather)\b",
         0.65),
    ],

    "attachments": [
        (r"(?:attachments?|documents?\s*attached|supporting\s*documents?)\s*[:\-]?\s*"
         r"([^\n]{3,150})",
         1.0),
        (r"(?:photos?|images?|police\s*report|medical\s*report)\s*"
         r"(?:attached|included|enclosed)\s*[:\-]?\s*([^\n]{0,80})",
         0.75),
    ],

    "initial_estimate": [
        (r"(?:initial\s*estimate|preliminary\s*estimate|initial\s*claim\s*estimate)\s*[:\-]?\s*"
         r"\$?\s*([\d,]+(?:\.\d{2})?)",
         1.0),
        (r"(?:total\s*(?:estimated\s*)?(?:damage|loss|claim)\s*(?:amount)?)\s*[:\-]?\s*"
         r"\$?\s*([\d,]+(?:\.\d{2})?)",
         0.8),
    ],
}


# ──────────────────────────────────────────────
# MAIN EXTRACTION FUNCTION
# ──────────────────────────────────────────────

def extract_fields(text: str) -> tuple[dict, dict, list]:
    """
    Run all regex patterns against the document text and return:

    Returns:
        extracted_fields  (dict)  – field_name → string value or None
        confidence_scores (dict)  – field_name → float (0.0–1.0)
        missing_fields    (list)  – mandatory fields that got None
    """
    extracted = {}
    confidence = {}

    for field, patterns in FIELD_PATTERNS.items():
        value, score = _run_patterns(text, patterns)
        extracted[field] = value
        confidence[field] = score

        if value:
            logger.debug(f"  [{field}] = '{value[:60]}...' (confidence: {score})"
                         if len(str(value)) > 60
                         else f"  [{field}] = '{value}' (confidence: {score})")
        else:
            logger.debug(f"  [{field}] = NOT FOUND")

    # Identify which mandatory fields are missing
    missing = [f for f in MANDATORY_FIELDS if not extracted.get(f)]

    logger.info(f"Extraction complete — "
                f"{len(extracted) - len(missing)}/{len(MANDATORY_FIELDS)} mandatory fields found, "
                f"{len(missing)} missing")

    return extracted, confidence, missing


def _run_patterns(text: str, patterns: list) -> tuple[str | None, float]:
    """
    Try each (pattern, confidence) pair in order.
    Return the first match and its confidence, or (None, 0.0).
    """
    for pattern, score in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            value = _clean_value(match.group(1))
            if value:                    # ignore empty strings after cleaning
                return value, score
    return None, 0.0


def _clean_value(raw: str) -> str:
    """
    Strip internal newlines and collapse extra whitespace in an extracted value.
    """
    cleaned = re.sub(r"\s*\n\s*", " ", raw)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()
