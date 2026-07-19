"""
Sysmex CS-2500 coagulation processor.

Reads Sysmex CS-2500 coagulation analyzer export CSVs, filters samples to
a user-specified date range, and for each of eight coagulation assays
(APTT, PT, Fibrinogen, Protien C, Protein S, D-Dimer, Antithrombin III,
Factor VIII) extracts the samples with a completed (non-error) result,
writing a combined summary + detail CSV.

FlowForge entrypoint: `process(input_files, form_data, output_dir)`.
"""
import os
from datetime import date, datetime
from typing import Dict, List, Union

import pandas as pd

from apps.processors.common import (
    build_output_filename,
    normalize_paths,
    read_and_concat_csvs,
    wrap_processing_errors,
)
from apps.workflows.services import ProcessingError

# Columns retained from the raw CS-2500 export before any processing.
COLUMNS_NEEDED = [
    'Status', 'Time', 'Sample_No.',
    'APTT', 'PT_INN~sec', 'Fbg_DT', 'PC_cl~%',
    'INN_FPS~%', 'INN_DDi', 'INN_AT~%', 'VIII_FS~%',
]

# Raw assay column -> display name used in the generated report. Iteration
# order here is the order detail blocks are written in; the summary
# counts end up in the *reverse* of this order, matching the original
# script (each assay's rows are prepended ahead of the previous ones).
ASSAY_COLUMN_RENAME = {
    'APTT': 'APTT',
    'PT_INN~sec': 'PT',
    'Fbg_DT': 'Fibrinogen',
    'PC_cl~%': 'Protien C',  # [sic] - kept to match the original script's
                             # (misspelled) output column name
    'INN_FPS~%': 'Protein S',
    'INN_DDi': 'D-Dimer',
    'INN_AT~%': 'Antithrombin III',
    'VIII_FS~%': 'Factor VIII',
}

# A sample's result for a given assay is dropped only if BOTH its Status
# is one of these AND that assay's raw value is one of the placeholder
# strings below - a Review or Error-status sample with an actual result
# for this specific assay is still kept.
ERROR_STATUSES = ['Error', 'On Hold']
ERROR_RESULT_PLACEHOLDERS = ['****.*', '***.**', '----.-']

# Raw Time column format, e.g. "22-03-2026 02:43".
TIME_FORMAT = '%d-%m-%Y %H:%M'


def process(input_files: Dict[str, Union[str, List[str]]], form_data: dict, output_dir: str) -> dict:
    """
    Process one or more Sysmex CS-2500 export CSVs into a combined
    summary + detail report of completed coagulation assay results within
    a date range.

    Args:
        input_files: Mapping of form field names to temp file path(s).
            Expected key:
                - 'cs2500_files': path or list of paths to CS-2500 CSVs.
        form_data: Other form field values submitted with the workflow.
            Expected keys:
                - 'start_date', 'end_date': the date range. Normally
                  datetime.date objects (from a "date" input_config field
                  type), but a 'YYYY-MM-DD' or 'DD-MM-YYYY' string is also
                  accepted as a fallback.
            An optional 'batch_label' key, if present and non-empty, is
            sanitized and included in the generated output filename.
        output_dir: Directory (already created by WorkflowProcessorService)
            that the generated report must be written into.

    Note:
        The date comparison mirrors the original script exactly: Time >
        start_date's midnight AND Time < end_date's midnight. In practice
        this makes start_date effectively inclusive (samples are almost
        never logged at exactly 00:00:00) and end_date genuinely
        exclusive - pick the day *after* the last day you want included.

    Returns:
        A dict mapping the output key to the generated file path, e.g.
        {'cs2500_report': '/path/to/output_dir/out_CS2500_May_2026.csv'}.

    Raises:
        ProcessingError: If required files/dates are missing, malformed,
            or processing otherwise fails. Raised with a user-friendly
            message.
    """
    file_paths = normalize_paths(input_files.get('cs2500_files'))
    if not file_paths:
        raise ProcessingError('No CS-2500 CSV files were uploaded.')

    form_data = form_data or {}
    start_time = _to_datetime(form_data.get('start_date'), 'Start Date')
    end_time = _to_datetime(form_data.get('end_date'), 'End Date')
    if start_time >= end_time:
        raise ProcessingError('Start Date must be earlier than End Date.')

    with wrap_processing_errors('CS-2500'):
        monthly_df = _load_and_filter(file_paths, start_time, end_time)

    if monthly_df.empty:
        raise ProcessingError(
            'No sample rows fall within the selected date range. '
            'Please check the dates and uploaded files.'
        )

    output_filename = build_output_filename('CS2500', form_data, start_time)
    output_path = os.path.join(output_dir, output_filename)

    with wrap_processing_errors('CS-2500'):
        final_df = _write_assay_detail_rows(monthly_df, output_path)

    # Prepend the summary counts above the detail rows, matching the
    # original script's output layout.
    new_content = final_df.count().to_string() + '\n \n'
    with open(output_path, 'r+', encoding='utf-8') as f:
        old_content = f.read()
        f.seek(0)
        f.write(new_content + old_content)

    return {'cs2500_report': output_path}


def _to_datetime(value, field_label: str) -> datetime:
    """
    Convert a date-range form field's value into a datetime.datetime at
    midnight, for comparison against the parsed Time column.

    Args:
        value: A datetime.date/datetime.datetime (the normal case, from a
            "date" input_config field), or a 'YYYY-MM-DD'/'DD-MM-YYYY'
            string as a fallback.
        field_label: Human-readable field name used in the error message.

    Returns:
        A datetime.datetime at 00:00:00 on that date.

    Raises:
        ProcessingError: If value is missing or isn't a recognizable date.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str) and value.strip():
        for fmt in ('%Y-%m-%d', '%d-%m-%Y'):
            try:
                return datetime.strptime(value.strip(), fmt)
            except ValueError:
                continue
    raise ProcessingError(f'{field_label} is missing or not a valid date.')


def _load_and_filter(file_paths: List[str], start_time: datetime, end_time: datetime) -> pd.DataFrame:
    """
    Read, clean, date-filter, and rename assay columns for a group of
    CS-2500 CSV files.

    Args:
        file_paths: CSV file paths to concatenate and clean.
        start_time: Samples with Time after this instant are kept.
        end_time: Samples with Time before this instant are kept.

    Returns:
        The cleaned, date-filtered DataFrame with assay columns renamed
        per ASSAY_COLUMN_RENAME, ready for per-assay detail extraction.
    """
    df = read_and_concat_csvs(file_paths)
    cleaned_df = df.filter(items=COLUMNS_NEEDED)

    # Sample_No. can contain non-numeric entries (QC labels, trailing
    # '*' flags) but guard against a batch where every value happens to
    # be numeric, which would otherwise read in as an int column.
    cleaned_df['Sample_No.'] = cleaned_df['Sample_No.'].astype(str)
    cleaned_df['Time'] = pd.to_datetime(cleaned_df['Time'], format=TIME_FORMAT)

    monthly_df = cleaned_df[
        (cleaned_df['Time'] > start_time) & (cleaned_df['Time'] < end_time)
    ].copy()
    monthly_df = monthly_df.rename(columns=ASSAY_COLUMN_RENAME)

    return monthly_df


def _write_assay_detail_rows(monthly_df: pd.DataFrame, output_path: str) -> pd.DataFrame:
    """
    For each assay column, filter to samples with a completed (non-error)
    result, append that assay's detail rows to output_path, and
    accumulate them for the final per-assay counts.

    Args:
        monthly_df: Output of _load_and_filter().
        output_path: File to append each assay's detail rows to.

    Returns:
        A DataFrame combining all assays' kept rows (each assay as its
        own column, populated only where that assay applied) - the
        caller computes final per-assay counts from this via .count().
    """
    final_df = pd.DataFrame()

    for column in monthly_df.columns[3:]:
        is_dropped = (
            monthly_df['Status'].isin(ERROR_STATUSES)
            & monthly_df[column].isin(ERROR_RESULT_PLACEHOLDERS)
        )
        assay_df = monthly_df[~is_dropped].filter(items=['Time', 'Sample_No.', column]).copy()
        assay_df[column] = assay_df[column].astype(str).apply(_data_convrt)
        assay_df = assay_df[assay_df[column] == 1]
        assay_df[column] = column

        assay_df.to_csv(output_path, mode='a', index=False)
        final_df = pd.concat([assay_df, final_df], ignore_index=True)

    return final_df


def _data_convrt(x) -> int:
    """
    Convert a raw CS-2500 assay result value into a 1/0 "was a usable
    result recorded" indicator.

    Returns 1 if the value parses as a positive float, or if it's a
    non-numeric string (e.g. a placeholder string that wasn't already
    filtered out by the Status check); returns 0 for zero, negative, or
    NaN/blank values.

    Args:
        x: The raw cell value from an assay column (already cast to str).

    Returns:
        1 or 0.
    """
    try:
        y = float(x)
        return 1 if y > 0 else 0
    except (TypeError, ValueError):
        return 1