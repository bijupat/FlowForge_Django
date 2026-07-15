"""
Example processor: Merge multiple CSV/XLSX files into a single merged XLSX report.

Inputs:
    - file1, file2: CSV or XLSX files with common columns.

Output:
    - merged_report: Merged XLSX file.
"""
import os
import logging
from typing import Dict, Any

import pandas as pd

logger = logging.getLogger('flowforge.processors.merge_reports')


def process(input_files: Dict[str, str], form_data: Dict[str, Any], output_dir: str) -> Dict[str, str]:
    """
    Merge two input files and produce a merged XLSX report.

    Args:
        input_files: Dict mapping field names to file paths.
        form_data: Dict of additional form field values (unused here).
        output_dir: Directory to save output files.

    Returns:
        Dict mapping output keys to output file paths.
    """
    if len(input_files) < 2:
        raise ValueError("This workflow requires exactly 2 input files to merge.")

    # Read input files
    file_paths = list(input_files.values())
    dfs = []
    for idx, path in enumerate(file_paths):
        try:
            if path.lower().endswith('.csv'):
                df = pd.read_csv(path)
            elif path.lower().endswith(('.xls', '.xlsx')):
                df = pd.read_excel(path)
            else:
                raise ValueError(f"Unsupported file format: {path}")
            dfs.append(df)
            logger.info(f"Read file {idx+1}: {os.path.basename(path)} with {len(df)} rows and {len(df.columns)} columns.")
        except Exception as e:
            logger.error(f"Failed to read file {path}: {e}")
            raise

    # Merge on common columns
    common_cols = set(dfs[0].columns)
    for df in dfs[1:]:
        common_cols &= set(df.columns)

    if not common_cols:
        raise ValueError("No common columns found between the input files.")

    common_cols = list(common_cols)
    logger.info(f"Merging on common columns: {common_cols}")

    merged_df = dfs[0]
    for df in dfs[1:]:
        merged_df = pd.merge(merged_df, df, on=common_cols, how='outer')

    # Generate output filename
    from datetime import datetime
    date_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_filename = f"merged_report_{date_str}.xlsx"
    output_path = os.path.join(output_dir, output_filename)

    # Save merged file
    merged_df.to_excel(output_path, index=False, engine='openpyxl')
    logger.info(f"Merged report saved to {output_path} ({len(merged_df)} rows)")

    return {
        'merged_report': output_path,
    }
