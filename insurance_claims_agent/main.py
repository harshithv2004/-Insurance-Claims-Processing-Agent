"""
main.py
-------
Entry point for the Autonomous Insurance Claims Processing Agent.

This script wires together all modules:
  utils.py      → file loading, JSON saving, CSV export
  extractor.py  → field extraction with confidence scores
  validator.py  → data validation and consistency checks
  router.py     → business routing rules

Usage examples:
  # Process one file and print the result:
  python main.py --file sample_data/claim_001_fast_track.txt

  # Process all files in a folder and save JSON + CSV:
  python main.py --folder sample_data/ --output outputs/

  # Silent mode (no per-step console prints):
  python main.py --folder sample_data/ --output outputs/ --quiet
"""

import argparse
import json
import os
import sys

from utils     import (load_document, clean_text, save_json,
                        build_output_path, export_summary_csv, logger)
from extractor import extract_fields
from validator import validate_fields, issues_to_dict_list
from router    import determine_route


# Default output directory
DEFAULT_OUTPUT_DIR = "outputs"


# ──────────────────────────────────────────────
# CORE PIPELINE
# ──────────────────────────────────────────────

def process_document(file_path: str, output_dir: str) -> dict:
    """
    Run the full processing pipeline for a single FNOL document.

    Pipeline:
      1. Load  – read PDF or TXT into raw text
      2. Clean – normalise whitespace
      3. Extract – pull out all field values + confidence scores
      4. Validate – check format and logical consistency
      5. Route – apply business rules to choose a queue
      6. Save – write result JSON to output_dir

    Args:
        file_path  : Path to the input document.
        output_dir : Directory to write the output JSON file.

    Returns:
        A result dict with keys:
          source_file, extractedFields, confidenceScores,
          missingFields, validationIssues, recommendedRoute, reasoning
    """
    logger.info(f"{'─'*55}")
    logger.info(f"Processing: {file_path}")

    # ── Step 1: Load ──────────────────────────────────────────────────
    try:
        raw_text = load_document(file_path)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        logger.error(f"Failed to load document: {e}")
        return _build_error_result(file_path, str(e))

    # ── Step 2: Clean ─────────────────────────────────────────────────
    clean = clean_text(raw_text)

    # ── Step 3: Extract ───────────────────────────────────────────────
    logger.info("Running field extraction...")
    extracted, confidence, missing = extract_fields(clean)

    # ── Step 4: Validate ──────────────────────────────────────────────
    logger.info("Running validation checks...")
    issues = validate_fields(extracted)

    # ── Step 5: Route ─────────────────────────────────────────────────
    logger.info("Determining route...")
    route, reasoning = determine_route(extracted, missing)

    # ── Step 6: Assemble and save result ─────────────────────────────
    result = {
        "source_file":      os.path.basename(file_path),
        "extractedFields":  extracted,
        "confidenceScores": confidence,
        "missingFields":    missing,
        "validationIssues": issues_to_dict_list(issues),
        "recommendedRoute": route,
        "reasoning":        reasoning,
    }

    out_path = build_output_path(file_path, output_dir)
    save_json(result, out_path)

    logger.info(f"Route: {route}")
    return result


def _build_error_result(file_path: str, error_message: str) -> dict:
    """Return a standard error result when a file cannot be processed."""
    return {
        "source_file":      os.path.basename(file_path),
        "extractedFields":  {},
        "confidenceScores": {},
        "missingFields":    ["(all – document could not be loaded)"],
        "validationIssues": [{"field": "file", "severity": "ERROR",
                               "message": error_message}],
        "recommendedRoute": "Manual Review",
        "reasoning":        f"Document processing failed: {error_message}",
    }


# ──────────────────────────────────────────────
# CLI ENTRY POINT
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Autonomous Insurance Claims Processing Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --file sample_data/claim_001_fast_track.txt
  python main.py --folder sample_data/ --output outputs/
  python main.py --folder sample_data/ --output outputs/ --quiet
        """
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--file",   type=str,
        help="Path to a single FNOL document (.pdf or .txt)"
    )
    group.add_argument(
        "--folder", type=str,
        help="Path to a folder containing FNOL documents"
    )

    parser.add_argument(
        "--output", type=str, default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory for JSON and CSV results (default: {DEFAULT_OUTPUT_DIR})"
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress verbose per-step console output"
    )

    args = parser.parse_args()
    os.makedirs(args.output, exist_ok=True)

    # ── Single file ───────────────────────────────────────────────────
    if args.file:
        result = process_document(args.file, args.output)
        if not args.quiet:
            print("\n" + "=" * 55)
            print("  RESULT")
            print("=" * 55)
            print(json.dumps(result, indent=2))

    # ── Batch folder ──────────────────────────────────────────────────
    elif args.folder:
        if not os.path.isdir(args.folder):
            logger.error(f"Folder not found: {args.folder}")
            sys.exit(1)

        supported_ext = (".pdf", ".txt")
        files = sorted([
            os.path.join(args.folder, f)
            for f in os.listdir(args.folder)
            if f.lower().endswith(supported_ext)
        ])

        if not files:
            logger.warning(f"No .pdf or .txt files found in: {args.folder}")
            sys.exit(0)

        logger.info(f"Found {len(files)} document(s) to process.")
        all_results = []

        for fp in files:
            result = process_document(fp, args.output)
            all_results.append(result)

        # Export CSV summary (bonus feature)
        csv_path = os.path.join(args.output, "batch_summary.csv")
        export_summary_csv(all_results, csv_path)

        # Print routing summary table
        if not args.quiet:
            print("\n" + "=" * 65)
            print("  ROUTING SUMMARY")
            print("=" * 65)
            print(f"  {'File':<40}  {'Route':<22}")
            print(f"  {'─'*38}  {'─'*20}")
            for r in all_results:
                fname = r.get("source_file", "unknown")[:40]
                route = r.get("recommendedRoute", "unknown")
                print(f"  {fname:<40}  {route:<22}")
            print("=" * 65)
            print(f"  Outputs saved to: {args.output}/")
            print(f"  Log file:         logs/claims_agent.log")


if __name__ == "__main__":
    main()
