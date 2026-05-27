# Autonomous Insurance Claims Processing Agent

A Python-based pipeline that reads FNOL (First Notice of Loss) insurance documents — both plain-text files and fillable ACORD 2 PDFs — extracts key claim fields, validates the data, applies business routing rules, and writes structured JSON output plus a CSV batch summary.

---

## Project structure

```
insurance_claims_agent/
│
├── main.py            # Entry point — orchestrates the full pipeline
├── extractor.py       # Regex-based field extraction with confidence scores
├── validator.py       # Data validation and consistency checks
├── router.py          # Business routing rules
├── utils.py           # File loading, logging, JSON saving, CSV export
│                      # (includes ACORD fillable-PDF support)
│
├── sample_data/       # 5 sample FNOL documents (.txt format)
│   ├── claim_001_fast_track.txt
│   ├── claim_002_manual_review.txt
│   ├── claim_003_specialist_queue.txt
│   ├── claim_004_investigation_flag.txt
│   └── claim_005_standard_review.txt
│
├── outputs/           # Generated JSON outputs + batch CSV summary
│   ├── claim_001_fast_track_output.json
│   ├── ...
│   └── batch_summary.csv
│
├── logs/
│   └── claims_agent.log
│
├── requirements.txt
└── README.md
```

---

## Requirements

- Python 3.10 or higher
- Dependencies listed in `requirements.txt`:

| Library       | Purpose                                              |
|---------------|------------------------------------------------------|
| `pdfplumber`  | Extract text and form fields from PDF files          |
| `pandas`      | Build and export the batch CSV summary               |
| `re`          | Regex patterns for all field extraction              |
| `json`        | Serialise and save result dictionaries               |
| `logging`     | Write structured logs to console and file            |
| `argparse`    | Command-line interface (`--file` / `--folder` flags) |
| `dataclasses` | Clean `ValidationIssue` data structure               |
| `datetime`    | Parse and compare dates for policy period checks     |

---

## Local setup (VS Code)

### 1. Open the project

Extract the project zip, then in VS Code: **File → Open Folder → `insurance_claims_agent/`**

### 2. Create and activate a virtual environment

Open the integrated terminal (`Ctrl+`` ` on Windows/Linux, `Cmd+`` ` on Mac):

```bash
# Create the virtual environment
python -m venv venv

# Activate it
# Windows:
venv\Scripts\activate

# Mac / Linux:
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Select the Python interpreter

Press `Ctrl+Shift+P` (or `Cmd+Shift+P` on Mac) → type **Python: Select Interpreter** → choose the interpreter inside your `venv` folder.

---

## Running the agent

### Process a single file

```bash
python main.py --file sample_data/claim_001_fast_track.txt
```

The result JSON is printed to the console and saved to `outputs/`.

### Process all files in a folder (batch mode)

```bash
python main.py --folder sample_data/ --output outputs/
```

Processes every `.pdf` and `.txt` file in the folder, saves one JSON per file, and creates `batch_summary.csv` in the output directory.

### Process a filled ACORD 2 PDF

```bash
python main.py --file path/to/your_acord_form.pdf --output outputs/
```

The agent automatically detects fillable ACORD PDFs and reads field values directly from the form annotations rather than the text layer. See [ACORD PDF support](#acord-pdf-support) below.

### Optional flags

```bash
# Custom output directory
python main.py --folder sample_data/ --output my_results/

# Suppress per-step console output (logs still written to file)
python main.py --folder sample_data/ --quiet
```

---

## Pipeline overview

Each document passes through five stages:

```
1. Load      → Read .txt or .pdf into raw text
               (fillable PDFs: values extracted from form annotations)

2. Clean     → Normalise whitespace for consistent regex matching

3. Extract   → Pull out all field values with confidence scores (0.0–1.0)

4. Validate  → Check formats, logical consistency, suspicious keywords

5. Route     → Apply business rules to assign a processing queue
```

---

## Field extraction

All extraction is done in `extractor.py` using Python's `re` module. Each field has a list of regex patterns ordered from most specific to most general. The extractor tries each in order and returns the first match.

**Confidence scores:**

| Score     | Meaning                                        |
|-----------|------------------------------------------------|
| 1.0       | Matched by the primary, most-specific pattern  |
| 0.75–0.9  | Matched by a secondary or fallback pattern     |
| 0.0       | Field not found in the document                |

**Fields extracted:**

| Category           | Fields                                                        |
|--------------------|---------------------------------------------------------------|
| Policy information | policy_number, policyholder_name, effective_dates             |
| Incident info      | incident_date, incident_time, incident_location, incident_description |
| Involved parties   | claimant_name, third_parties, contact_details                 |
| Asset details      | asset_type, asset_id, estimated_damage                        |
| Other mandatory    | claim_type, attachments, initial_estimate                     |

---

## Routing logic

Rules are evaluated in priority order (highest first):

| Priority | Route               | Trigger                                                      |
|----------|---------------------|--------------------------------------------------------------|
| 1        | Investigation Flag  | Description contains fraud/suspicious keywords               |
| 2        | Manual Review       | One or more mandatory fields missing                         |
| 3        | Specialist Queue    | Claim type or description indicates bodily injury            |
| 4        | Fast-track          | All fields present, no flags, damage < $25,000               |
| 5        | Standard Review     | All fields present, no flags, damage ≥ $25,000 (default)    |

---

## Validation checks

`validator.py` runs these checks beyond simple missing-field detection:

- Date format correctness (MM/DD/YYYY)
- Phone number and email format
- VIN length and invalid characters (I, O, Q)
- Damage vs. initial estimate consistency (> 10% difference flagged)
- Incident date within policy effective period
- Suspicious keywords in description
- Damage amounts over $500,000 (possible data-entry error)

---

## Output format

Each processed claim produces a JSON file:

```json
{
  "source_file": "claim_001_fast_track.txt",
  "extractedFields": {
    "policy_number": "POL-2024-004821",
    "policyholder_name": "Sarah Elizabeth Mitchell",
    "effective_dates": "01/15/2024 to 01/15/2025",
    "incident_date": "03/12/2024",
    "incident_time": "02:30 PM",
    "incident_location": "Corner of 5th Avenue and Oak Street, Austin, TX 78701",
    "incident_description": "The insured vehicle was stationary at a red light...",
    "claimant_name": "Sarah Mitchell",
    "third_parties": "James R. Holloway",
    "contact_details": "(512) 555-0193",
    "asset_type": "Sedan",
    "asset_id": "1HGCV1F30MA123456",
    "estimated_damage": "8,200",
    "claim_type": "Collision",
    "attachments": "Police report #APD-20240312-0047, 4 photographs",
    "initial_estimate": "8,200"
  },
  "confidenceScores": {
    "policy_number": 1.0,
    "policyholder_name": 1.0,
    "...": "..."
  },
  "missingFields": [],
  "validationIssues": [],
  "recommendedRoute": "Fast-track",
  "reasoning": "All mandatory fields are present. No fraud keywords or injury indicators found. Estimated damage $8,200.00 is below the $25,000 threshold — eligible for fast-track processing."
}
```

Batch mode also produces `batch_summary.csv` with one row per claim, ready for review in Excel or further analysis.

---

## Sample inputs and expected routes

| File                             | Scenario                   | Expected route     |
|----------------------------------|----------------------------|--------------------|
| claim_001_fast_track.txt         | Complete, low damage       | Fast-track         |
| claim_002_manual_review.txt      | Missing name, VIN, dates   | Manual Review      |
| claim_003_specialist_queue.txt   | Bodily injury claim        | Specialist Queue   |
| claim_004_investigation_flag.txt | Fraud/staged indicators    | Investigation Flag |
| claim_005_standard_review.txt    | Complete, high damage      | Standard Review    |

---

## ACORD PDF support

The agent supports filled **ACORD 2 Automobile Loss Notice** PDFs (the standard industry form). Fillable PDFs store entered values as widget annotations — not in the visible text layer — so standard text extraction returns only the blank form labels.

`utils.py` detects fillable ACORD forms automatically and reads values directly from the PDF form fields, synthesising a structured text document that the regex extractor can parse. No changes to `extractor.py`, `validator.py`, or `router.py` are needed.

**To use:** simply point `--file` at your filled ACORD PDF. The detection and extraction are automatic.

---

## Error handling

| Error                | Cause                                         | Behaviour                        |
|----------------------|-----------------------------------------------|----------------------------------|
| `FileNotFoundError`  | Path does not exist                           | Graceful Manual Review result    |
| `ValueError`         | Unsupported file extension                    | Graceful Manual Review result    |
| `RuntimeError`       | PDF has no extractable text (scanned image)   | Graceful Manual Review result    |
| `UnicodeDecodeError` | TXT file not UTF-8                            | Falls back to latin-1 encoding   |

---

## Limitations and possible improvements

- **Scanned PDFs** require OCR (e.g. `pytesseract`) — not included to keep the project lightweight.
- **NLP-based extraction** (spaCy NER) would improve name and location accuracy over regex alone.
- **Confidence thresholds** could auto-route low-confidence extractions to Manual Review.
- **Database integration** (SQLite / PostgreSQL) would allow tracking claims over time rather than flat JSON files.
- **ACORD effective dates** are not stored on a dedicated field in the ACORD 2 form and must be present in a remarks or description field, or supplied externally.
