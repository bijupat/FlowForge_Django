"""
Alinity QC/Sample processor.

Reads Abbott Alinity Sample and QC (control) CSV exports, cleans and
aggregates them by assay, and writes a combined summary + detail CSV.

FlowForge entrypoint: `process(input_files, form_data, output_dir)`.
"""
import os
from typing import Dict, List, Tuple, Union

import pandas as pd

from apps.processors.common import (
    build_output_filename,
    normalize_paths,
    read_and_concat_csvs,
    wrap_processing_errors,
)
from apps.workflows.services import ProcessingError

# Columns retained from the raw Alinity export before any processing.
COLUMNS_NEEDED = [
    'Sample_ID',
    'Assay_Name',
    'Date_of_Completion',
    'Time_of_Completion',
    'Operator_ID',
    'Control_Name',
    'Control_Level',
    'Control_Lot',
]

# Some Alinity export layouts use different raw header names for the same
# underlying field (e.g. an LIS-style "Sample Results" export uses 'SID'
# and 'ASSAY' where the QC-summary export uses 'Sample_ID'/'Assay_Name').
# The first alias found on a given file wins; if the canonical name is
# already present as-is, no renaming happens.
COLUMN_ALIASES: Dict[str, List[str]] = {
    'Sample_ID': ['SID'],
    'Assay_Name': ['ASSAY'],
    'Operator_ID': ['OPERATOR_ID'],
    'Control_Name': ['CONTROL_NAME'],
    'Control_Level': ['CONTROL_LEVEL'],
    'Control_Lot': ['CONTROL_LOT'],
}

# Some export layouts report completion date and time as a single merged
# column instead of two separate ones. Checked in order; the same
# '%d.%m.%Y %H:%M' format is used either way since both layouts observed
# so far share it.
MERGED_DATETIME_ALIASES = ['DATE/TIME_COMPLETED']


def process(input_files: Dict[str, Union[str, List[str]]], form_data: dict, output_dir: str) -> dict:
    """
    Process one or more Alinity QC files and Sample files into a combined
    summary report.

    Note:
        This requires the workflow's input_config to mark both file fields
        with "multiple": true, and apps/workflows/forms.py, views.py, and
        services.py to support multi-file fields (MultipleFileField,
        request.FILES.getlist, and list-of-paths saving respectively).
        Without those platform changes, input_files values here would only
        ever contain a single path.

    Args:
        input_files: Mapping of form field names to temp file path(s).
            Expected keys:
                - 'sample_files': path or list of paths to Sample CSVs.
                - 'qc_files': path or list of paths to QC/control CSVs.
        form_data: Any other form field values submitted with the workflow.
            An optional 'batch_label' key, if present and non-empty, is
            sanitized and included in the generated output filename (e.g.
            "out_alinity_SiteA_Jul_2026.csv") so runs on different input
            files don't all download with an identical filename.
        output_dir: Directory (already created by WorkflowProcessorService)
            that the generated report must be written into. Every workflow
            processor is called with this keyword argument, since
            process_workflow() validates returned paths against it and
            WorkflowDownloadView only serves files under this directory.

    Returns:
        A dict mapping the output key to the generated file path, e.g.
        {'alinity_report': '/path/to/output_dir/out_alinity_Jul_2026.csv'}.

    Raises:
        ProcessingError: If required files are missing, malformed, or
            processing otherwise fails. Raised with a user-friendly message.
    """
    sample_paths = normalize_paths(input_files.get('sample_files'))
    qc_paths = normalize_paths(input_files.get('qc_files'))

    if not sample_paths:
        raise ProcessingError('No Sample files were uploaded.')
    if not qc_paths:
        raise ProcessingError('No QC (control) files were uploaded.')

    with wrap_processing_errors('Alinity'):
        assaywise_df, tests_count = _build_assay_summary(
            sample_paths, drop_columns=['Operator_ID', 'Time']
        )
        assaywise_df_c, tests_count_c = _build_assay_summary(
            qc_paths,
            drop_columns=['Operator_ID', 'Time', 'Control_Name', 'Control_Level', 'Control_Lot'],
        )

    start_time = assaywise_df['Time'].iloc[0]

    output_filename = build_output_filename('alinity', form_data, start_time)
    output_path = os.path.join(output_dir, output_filename)

    # Write summary counts first, then detailed assay-wise rows for both groups.
    tests_count.to_csv(output_path, mode='w')
    tests_count_c.to_csv(output_path, mode='a')
    assaywise_df.to_csv(output_path, mode='a', index=False)
    assaywise_df_c.to_csv(output_path, mode='a', index=False)

    return {'alinity_report': output_path}


def _build_assay_summary(
    file_paths: List[str], drop_columns: List[str]
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Read, clean, and aggregate a group of Alinity CSV files by assay.

    Tolerates two export layout differences seen in practice:
        - Alternate raw column names for the same field (see
          COLUMN_ALIASES), e.g. 'SID' instead of 'Sample_ID'.
        - Completion date and time as either two separate columns
          (Date_of_Completion + Time_of_Completion) or a single merged
          column (see MERGED_DATETIME_ALIASES).

    Args:
        file_paths: CSV file paths to concatenate and clean.
        drop_columns: Columns to exclude before computing per-assay counts
            (e.g. non-QC files drop fewer columns than QC files). Any
            column in this list that isn't present is silently skipped,
            since some export layouts (e.g. Sample-only LIS exports) don't
            have Control_Name/Control_Level/Control_Lot at all.

    Returns:
        Tuple of (assaywise_df, tests_count):
            - assaywise_df: cleaned, sorted rows with a combined Time column.
            - tests_count: per-assay row counts after dropping drop_columns.
    """
    df = read_and_concat_csvs(file_paths)
    df = _canonicalize_columns(df)

    cleaned_df = df.filter(items=COLUMNS_NEEDED)
    cleaned_df['Time'] = _resolve_completion_time(df)
    cleaned_df = cleaned_df.drop(columns=['Date_of_Completion', 'Time_of_Completion'], errors='ignore')

    assaywise_df = cleaned_df.sort_values(by=['Assay_Name', 'Time'], ignore_index=True)
    tests_count = assaywise_df.drop(columns=drop_columns, errors='ignore').groupby('Assay_Name').count()

    return assaywise_df, tests_count


def _canonicalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Rename known alternate raw column names to the canonical names this
    processor expects (see COLUMN_ALIASES), so exports using a different
    naming convention for the same field still work.

    Args:
        df: The raw (post header-cleaning) DataFrame.

    Returns:
        The DataFrame with any recognized alias columns renamed to their
        canonical name. Unrecognized columns are left untouched.
    """
    rename_map = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        if canonical in df.columns:
            continue
        for alias in aliases:
            if alias in df.columns:
                rename_map[alias] = canonical
                break
    return df.rename(columns=rename_map) if rename_map else df


def _resolve_completion_time(df: pd.DataFrame) -> pd.Series:
    """
    Build the completion Time column, supporting both a two-column export
    layout (Date_of_Completion + Time_of_Completion) and a single merged
    column layout (see MERGED_DATETIME_ALIASES).

    Args:
        df: DataFrame after column-header cleaning and alias canonicalization
            (i.e. before filtering down to COLUMNS_NEEDED, so a
            merged-datetime column that isn't in COLUMNS_NEEDED is still
            present to check for).

    Returns:
        A parsed datetime Series, same length and index as df.

    Raises:
        KeyError: If neither the two-column nor merged-column layout's
            expected column(s) are present.
    """
    if 'Date_of_Completion' in df.columns and 'Time_of_Completion' in df.columns:
        combined = df['Date_of_Completion'].astype(str) + ' ' + df['Time_of_Completion'].astype(str)
        return pd.to_datetime(combined, format='%d.%m.%Y %H:%M')

    for alias in MERGED_DATETIME_ALIASES:
        if alias in df.columns:
            return pd.to_datetime(df[alias].astype(str), format='%d.%m.%Y %H:%M')

    raise KeyError("'Date_of_Completion'/'Time_of_Completion' (or a merged completion date/time column)")