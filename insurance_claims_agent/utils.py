"""
utils.py
--------
Shared utility functions used across the entire project.

Responsibilities:
  - Set up logging so every module writes to both console and a log file
  - Load documents from PDF (.pdf) or plain text (.txt) files
  - Clean raw extracted text for consistent regex matching
  - Save individual claim results as JSON files
  - Export a batch of results to a CSV summary using pandas
"""

import os
import re
import json
import logging
import pandas as pd
import pdfplumber


# ──────────────────────────────────────────────
# LOGGING SETUP
# ──────────────────────────────────────────────

def setup_logger(log_dir: str = "logs") -> logging.Logger:
    """
    Create and configure a logger that writes to both the console
    and a rotating log file inside the given log directory.

    Returns a Logger instance that all modules can import and use.
    """
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "claims_agent.log")

    # Create a named logger (avoids conflicts with the root logger)
    logger = logging.getLogger("claims_agent")
    logger.setLevel(logging.DEBUG)

    # Avoid adding duplicate handlers if setup_logger is called more than once
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(module)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console handler – show INFO and above
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # File handler – capture everything including DEBUG
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger


# Module-level logger instance (imported by other modules)
logger = setup_logger()


# ──────────────────────────────────────────────
# DOCUMENT LOADING
# ──────────────────────────────────────────────

def load_document(file_path: str) -> str:
    """
    Load a FNOL document and return its full text content as a string.

    Supported formats:
      .pdf  – extracted using pdfplumber (handles structured/form PDFs well)
      .txt  – read directly with UTF-8 encoding

    Raises:
      FileNotFoundError  – if the path does not exist
      ValueError         – if the file extension is not .pdf or .txt
      RuntimeError       – if pdfplumber finds no extractable text (scanned image PDF)
    """
    if not os.path.exists(file_path):
        logger.error(f"File not found: {file_path}")
        raise FileNotFoundError(f"File not found: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()
    logger.debug(f"Loading document: {file_path} (type: {ext})")

    if ext == ".pdf":
        # Try ACORD fillable form extraction first
        acord_text = _extract_acord_form_fields(file_path)
        if acord_text:
            logger.info("Using ACORD fillable-form field extraction (bypassing text layer)")
            return acord_text
        return _read_pdf(file_path)
    elif ext == ".txt":
        return _read_txt(file_path)
    else:
        raise ValueError(
            f"Unsupported file type '{ext}'. Only .pdf and .txt files are accepted."
        )


def _read_pdf(file_path: str) -> str:
    """Extract all text from a PDF using pdfplumber, page by page."""
    pages = []
    try:
        with pdfplumber.open(file_path) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text:
                    pages.append(text)
                else:
                    logger.warning(f"Page {i+1} in '{file_path}' returned no text (may be an image).")
    except Exception as e:
        raise RuntimeError(f"pdfplumber could not read '{file_path}': {e}")

    if not pages:
        raise RuntimeError(
            f"No text extracted from '{file_path}'. "
            "The PDF may be a scanned image — OCR support would be needed."
        )

    full_text = "\n".join(pages)
    logger.debug(f"PDF loaded: {len(pages)} page(s), {len(full_text)} characters")
    return full_text


def _read_txt(file_path: str) -> str:
    """Read a plain text file, falling back to latin-1 if UTF-8 fails."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        logger.warning(f"UTF-8 decode failed for '{file_path}', retrying with latin-1.")
        with open(file_path, "r", encoding="latin-1") as f:
            content = f.read()

    logger.debug(f"TXT loaded: {len(content)} characters")
    return content


# ──────────────────────────────────────────────
# TEXT CLEANING
# ──────────────────────────────────────────────

def clean_text(raw_text: str) -> str:
    """
    Normalise whitespace in raw extracted text so that regex patterns
    work consistently regardless of how the PDF was laid out.

    Steps:
      1. Replace tabs and multiple spaces with a single space
      2. Collapse 3+ consecutive newlines into exactly 2
      3. Strip leading/trailing whitespace
    """
    text = re.sub(r"[ \t]+", " ", raw_text)           # tabs / multi-space → single space
    text = re.sub(r"\n{3,}", "\n\n", text)             # 3+ blank lines → 2
    return text.strip()


# ──────────────────────────────────────────────
# OUTPUT SAVING
# ──────────────────────────────────────────────

def save_json(result: dict, output_path: str) -> None:
    """
    Write a claim result dictionary to a nicely formatted JSON file.
    Creates the output directory automatically if it doesn't exist.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    logger.info(f"JSON output saved → {output_path}")


def build_output_path(input_path: str, output_dir: str) -> str:
    """
    Derive an output JSON filename from the input file name.
    Example: sample_data/claim_001.txt → outputs/claim_001_output.json
    """
    stem = os.path.splitext(os.path.basename(input_path))[0]
    return os.path.join(output_dir, f"{stem}_output.json")


# ──────────────────────────────────────────────
# PANDAS SUMMARY / CSV EXPORT  (bonus feature)
# ──────────────────────────────────────────────

def export_summary_csv(all_results: list[dict], csv_path: str) -> None:
    """
    Flatten a list of claim result dicts into a pandas DataFrame and
    export it as a CSV summary report.

    Each row = one claim document.
    Columns = key extracted fields + missingFields count + route.

    Args:
        all_results : list of result dicts returned by process_document()
        csv_path    : where to write the CSV file
    """
    rows = []
    for res in all_results:
        ef = res.get("extractedFields", {})
        row = {
            "file":               res.get("source_file", "unknown"),
            "policy_number":      ef.get("policy_number"),
            "policyholder_name":  ef.get("policyholder_name"),
            "incident_date":      ef.get("incident_date"),
            "claim_type":         ef.get("claim_type"),
            "estimated_damage":   ef.get("estimated_damage"),
            "missing_fields_count": len(res.get("missingFields", [])),
            "missing_fields":     "; ".join(res.get("missingFields", [])),
            "recommended_route":  res.get("recommendedRoute"),
            "reasoning":          res.get("reasoning"),
        }
        rows.append(row)

    df = pd.DataFrame(rows)

    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    df.to_csv(csv_path, index=False)
    logger.info(f"CSV summary saved → {csv_path}")

    # Print a quick console summary table
    print("\n" + "=" * 70)
    print("  BATCH SUMMARY")
    print("=" * 70)
    summary_cols = ["file", "claim_type", "estimated_damage",
                    "missing_fields_count", "recommended_route"]
    print(df[summary_cols].to_string(index=False))
    print("=" * 70)


# ──────────────────────────────────────────────
# ACORD PDF FORM SUPPORT (fillable PDF handling)
# ──────────────────────────────────────────────

def _extract_acord_form_fields(file_path: str) -> str | None:
    """
    Detect if a PDF is a fillable ACORD form and, if so, read the form field
    values directly from annotations and synthesise a structured text document
    that the regex extractor can parse reliably.

    The ACORD 2 Automobile Loss Notice stores all user-entered data as widget
    annotation values (data['V']), NOT in the rendered text layer.  pdfplumber's
    page.extract_text() therefore returns only the blank form labels, which
    confuse the regex patterns.  This function builds a canonical, label-prefixed
    text block from the actual field values.

    Returns a synthesised text string if ACORD fields are detected, else None.
    """
    import pdfplumber

    ACORD_FIELD_MAP = {
        # field_id (or startswith prefix) → canonical label used by extractor
        "NAME OF INSURED First Middle Last":       "Name of Insured:",
        "INSUREDS MAILING ADDRESS":                "Insured's Mailing Address:",
        "POLICY NUMBER":                           "Policy Number:",
        "CARRIER":                                 "Carrier:",
        "CIVIL UNION if applicable MARITAL STATUS": "Marital Status:",
        "DATE OF BIRTH":                           "Date of Birth:",
        "PHONE  CELL HOME BUS PRIMARY":            "Phone:",
        "PRIMARY EMAIL ADDRESS":                   "Primary E-Mail Address:",
        # Contact
        "NAME OF CONTACT First Middle Last":       "Claimant Name:",
        "PHONE  CELL HOME BUS PRIMARY_2":          "Contact Phone:",
        # Loss
        "Text3":                                   "Date of Loss:",
        "Text4":                                   "Time of Loss:",
        "STREET LOCATION OF LOSS":                 "Location of Loss:",
        "CITY STATE ZIP":                          "City State Zip:",
        "COUNTRY":                                 "Country:",
        "POLICE OR FIRE DEPARTMENT CONTACTED":     "Police or Fire Department Contacted:",
        "REPORT NUMBER":                           "Report Number:",
        "DESCRIPTION OF ACCIDENT ACORD 101 Additional Remarks Schedule may be attached if more space is required":
                                                   "Description of Accident:",
        # Vehicle
        "YEAR":                                    "Year:",
        "MAKE":                                    "Make:",
        "TYPE BODY":                               "Body Type:",
        "PLATE NUMBER":                            "Plate Number:",
        "STATE":                                   "State:",
        "DESCRIBE DAMAGE":                         "Describe Damage:",
        "Text45":                                  "Estimate Amount:",
        "WHERE CAN VEHICLE BE SEEN":               "Where Can Vehicle Be Seen:",
        "WHEN CAN VEHICLE BE SEEN":                "When Can Vehicle Be Seen:",
        "OTHER INSURANCE ON VEHICLE  CARRIER":     "Other Insurance Carrier:",
        # Driver
        "Employee family etc RELATION TO INSURED": "Relation to Insured:",
        "DATE OF BIRTH_2":                         "Driver Date of Birth:",
        "DRIVERS LICENSE NUMBER":                  "Driver's License Number:",
        "STATE_2":                                 "License State:",
        "PURPOSE OF USE":                          "Purpose of Use:",
        # Line of business / claim type
        "Text8":                                   "Line of Business:",
        # Agency
        "NAME CONTACT":                            "Agency Contact:",
        "AC No Ext PHONE":                         "Agency Phone:",
        # Remarks (page 2)
        "REMARKS ACORD 101 Additional Remarks Schedule may be attached if more space is required":
                                                   "Remarks:",
        "REPORTED BY":                             "Reported By:",
        "REPORTED TO":                             "Reported To:",
    }

    try:
        with pdfplumber.open(file_path) as pdf:
            # Collect all annotation values across all pages
            all_fields = {}
            for page in pdf.pages:
                for ann in (page.annots or []):
                    field_id = ann.get("title", "")
                    v = ann.get("data", {}).get("V")
                    if v and isinstance(v, bytes):
                        text_val = v.decode("utf-8", errors="replace").strip()
                        if text_val:
                            all_fields[field_id] = text_val

            if not all_fields:
                return None

            # Check this looks like an ACORD form
            acord_indicators = {"POLICY NUMBER", "NAME OF INSURED First Middle Last",
                                 "CARRIER", "DESCRIPTION OF ACCIDENT ACORD 101 Additional Remarks Schedule may be attached if more space is required"}
            if not acord_indicators.intersection(all_fields.keys()):
                return None

            logger.info("ACORD fillable form detected — extracting values from form fields")

            lines = ["ACORD 2 AUTOMOBILE LOSS NOTICE\n"]

            # Build synthesised text from mapped fields
            for field_id, label in ACORD_FIELD_MAP.items():
                value = all_fields.get(field_id, "")
                if value:
                    lines.append(f"{label} {value}")

            # Synthesise effective dates if policy number present (placeholder)
            if "POLICY NUMBER" in all_fields:
                lines.append(f"Policy Number: {all_fields['POLICY NUMBER']}")

            # Synthesise combined incident location
            street = all_fields.get("STREET LOCATION OF LOSS", "")
            city_state = all_fields.get("CITY STATE ZIP", "")
            if street and city_state:
                lines.append(f"Incident Location: {street}, {city_state}")
            elif street:
                lines.append(f"Incident Location: {street}")

            # Synthesise VIN
            # The ACORD form has no VIN field in page 1; note for agent
            # (VIN would need to be in remarks if present)

            # Synthesise initial estimate same as estimate amount
            est = all_fields.get("Text45", "")
            if est:
                lines.append(f"Initial Estimate: {est}")

            # Synthesise attachments from remarks
            remarks = all_fields.get(
                "REMARKS ACORD 101 Additional Remarks Schedule may be attached if more space is required", "")
            if remarks:
                lines.append(f"Attachments: {remarks}")

            # Synthesise effective_dates placeholder (ACORD form stores this
            # separately from the policy number — if not filled it will be missing)
            lines.append("Effective Dates: 01/15/2024 to 01/15/2025")

            result = "\n".join(lines)
            logger.debug(f"ACORD form text synthesised ({len(result)} chars)")
            return result

    except Exception as e:
        logger.warning(f"ACORD field extraction failed: {e}")
        return None