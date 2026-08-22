"""
Sysmex XN-1000 CBC/DIFF processor.

Reads Sysmex XN-1000 export CSVs, cleans them, and splits samples into
test categories (CBC, CBCDC, BACKGROUND, FUNC_ERR, and RETIC when the
export includes a reticulocyte channel), writing a combined summary +
detail CSV.

FlowForge entrypoint: `process(input_files, form_data, output_dir)`.
"""
import os
import re
from typing import Dict, List, Tuple, Union

import pandas as pd

from apps.processors.common import (
    build_output_filename,
    normalize_paths,
    read_and_concat_csvs,
    wrap_processing_errors,
)
from apps.workflows.services import ProcessingError

# Columns retained from the raw XN-1000 export before any processing.
COLUMNS_NEEDED = ['Sample_No.', 'Date', 'Time', 'RET%(%)', 'NEUT%(%)', 'Error(Func.)']
FINAL_COLUMNS_NEEDED = ['Sample_No.', 'DateTime', 'Test']

# Raw Date+Time formats seen across different XN-1000 export
# configurations, tried in order for each row independently (not per
# file) so a batch mixing files from both export configurations still
# parses correctly.
DATETIME_FORMATS = [
    '%d-%m-%Y %H:%M:%S',  # e.g. "22-03-2026 02:43:00"
    '%Y/%m/%d %H:%M:%S',  # e.g. "2026/07/01 22:25:22"
]


def process(input_files: Dict[str, Union[str, List[str]]], form_data: dict, output_dir: str) -> dict:
    """
    Process one or more Sysmex XN-1000 export CSVs into a classified
    summary report (CBC, CBCDC, BACKGROUND, FUNC_ERR, and RETIC if the
    export includes a RET%(%) reticulocyte column).

    Note:
        This requires the workflow's input_config to mark the file field
        with "multiple": true, and apps/workflows/forms.py, views.py, and
        services.py to support multi-file fields (MultipleFileField,
        request.FILES.getlist, and list-of-paths saving respectively).
        Without those platform changes, input_files values here would only
        ever contain a single path.

    Args:
        input_files: Mapping of form field names to temp file path(s).
            Expected key:
                - 'xn_files': path or list of paths to XN-1000 export CSVs.
        form_data: Any other form field values submitted with the workflow.
            An optional 'batch_label' key, if present and non-empty, is
            sanitized and included in the generated output filename (e.g.
            "out_XN1000_SiteA_Jul_2026.csv") so runs on different input
            files don't all download with an identical filename.
        output_dir: Directory (already created by WorkflowProcessorService)
            that the generated report must be written into. Every workflow
            processor is called with this keyword argument, since
            process_workflow() validates returned paths against it and
            WorkflowDownloadView only serves files under this directory.

    Returns:
        A dict mapping the output key to the generated file path, e.g.
        {'xn_report': '/path/to/output_dir/out_XN1000_Jul_2026.csv'}.

    Raises:
        ProcessingError: If required files are missing, malformed, or
            processing otherwise fails. Raised with a user-friendly message.
    """
    file_paths = normalize_paths(input_files.get('xn_files'))

    if not file_paths:
        raise ProcessingError('No XN-1000 CSV files were uploaded.')

    with wrap_processing_errors('XN-1000'):
        cleaned_df = _load_and_clean(file_paths)
        final_dfs = _classify_samples(cleaned_df)

    if cleaned_df.empty:
        raise ProcessingError('No valid sample rows remained after cleaning the uploaded files.')

    start_time = cleaned_df['DateTime'].iloc[0]
    output_filename = build_output_filename('XN1000', form_data, start_time)
    output_path = os.path.join(output_dir, output_filename)

    new_content = ''
    for name, df in final_dfs.items():
        df = df.copy()
        df['Test'] = name
        df = df.filter(items=FINAL_COLUMNS_NEEDED)
        df.to_csv(output_path, mode='a', index=False)
        new_content += f'{name}, {len(df)}\n'

    # Prepend the summary counts above the detail rows, matching the
    # original script's output layout.
    with open(output_path, 'r+', encoding='utf-8') as f:
        old_content = f.read()
        f.seek(0)
        f.write(new_content + old_content)

    return {'xn_report': output_path}


def _parse_datetime(combined: pd.Series) -> pd.Series:
    """
    Parse a combined "Date Time" string column into datetimes, tolerating
    more than one raw export format (see DATETIME_FORMATS).

    Each format is tried in turn against whatever rows haven't parsed yet,
    so a single batch mixing files from different export configurations
    (e.g. one XN-1000 file using 'DD-MM-YYYY' dates and another using
    'YYYY/MM/DD' dates) still parses every row correctly - this is
    per-row, not per-file, since nothing about a merged CSV batch
    guarantees one format per file boundary either.

    Args:
        combined: A Series of "Date Time" strings, e.g. "22-03-2026 02:43:00"
            or "2026/07/01 22:25:22".

    Returns:
        A parsed datetime Series, same length and index as combined.

    Raises:
        ValueError: If any row doesn't match any of DATETIME_FORMATS,
            naming a sample of the unparseable raw values so the message
            is actionable rather than a generic pandas error.
    """
    result = pd.Series(pd.NaT, index=combined.index, dtype='datetime64[ns]')
    remaining_mask = pd.Series(True, index=combined.index)

    for fmt in DATETIME_FORMATS:
        if not remaining_mask.any():
            break
        parsed = pd.to_datetime(combined[remaining_mask], format=fmt, errors='coerce')
        newly_parsed = parsed.notna()
        result.loc[parsed.index[newly_parsed]] = parsed[newly_parsed]
        remaining_mask.loc[parsed.index[newly_parsed]] = False

    if remaining_mask.any():
        bad_samples = combined[remaining_mask].unique()[:5]
        raise ValueError(
            "Unrecognized Date/Time format in "
            f"{int(remaining_mask.sum())} row(s), e.g.: {list(bad_samples)}. "
            f"Expected one of: {DATETIME_FORMATS}."
        )

    return result


def _load_and_clean(file_paths: List[str]) -> pd.DataFrame:
    """
    Read, concatenate, and clean a group of XN-1000 CSV files.

    Args:
        file_paths: CSV file paths to concatenate and clean.

    Returns:
        A cleaned, sorted DataFrame with a combined DateTime column, rows
        containing 'ERR' in Sample_No. removed.
    """
    df = read_and_concat_csvs(file_paths)

    cleaned_df = df.filter(items=COLUMNS_NEEDED)
    # Sample_No. can read in as numeric (e.g. a long sample ID with no
    # letters) rather than text, which would break the .str accessor calls
    # below and in _classify_samples's BACKGROUNDCHECK check.
    cleaned_df['Sample_No.'] = cleaned_df['Sample_No.'].astype(str)
    combined = cleaned_df['Date'].astype(str) + ' ' + cleaned_df['Time'].astype(str)
    cleaned_df['DateTime'] = _parse_datetime(combined)
    cleaned_df = cleaned_df.drop(columns=['Date', 'Time'])

    # Drop error samples. na=False guards against a NaN Sample_No. raising
    # instead of just being treated as "doesn't contain ERR".
    cleaned_df = cleaned_df[~cleaned_df['Sample_No.'].str.contains('ERR', na=False)]
    cleaned_df = cleaned_df.sort_values(by=['DateTime'], ignore_index=True)

    return cleaned_df


def _split_by_mask(df: pd.DataFrame, mask: pd.Series) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Split a dataframe into (rows where mask is False, rows where mask is
    True) using boolean indexing rather than groupby.

    The original script used `[x for _, x in df.groupby(mask)]`, which
    raises ValueError whenever a split has only one side (e.g. a batch
    with zero functional-error samples, or zero BACKGROUNDCHECK rows) -
    a case real XN-1000 exports hit often. Boolean indexing instead
    always returns two DataFrames, the empty one simply having 0 rows.

    Args:
        df: The dataframe to split.
        mask: A boolean Series aligned to df's index.

    Returns:
        Tuple of (false_group, true_group).
    """
    return df[~mask], df[mask]


def _classify_samples(cleaned_df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """
    Split the cleaned dataframe into XN-1000 test categories, mirroring the
    original script's sequential splits.

    Some XN-1000 export configurations (e.g. a CBC+DIFF-only run with no
    reticulocyte channel) omit the RET%(%) column entirely. When present,
    samples are split into CBC/CBCDC via the RET%(%) flag first and then
    the NEUT%(%) flag, producing a separate RETIC category. When absent,
    the RETIC split is skipped and CBC/CBCDC are split directly on the
    NEUT%(%) flag instead.

    Args:
        cleaned_df: Output of _load_and_clean().

    Returns:
        Dict mapping test category name to its DataFrame. Always includes
        CBC, CBCDC, BACKGROUND, FUNC_ERR (any of which may be empty if
        the batch had none of that category); RETIC is included only if
        the source file had a RET%(%) column.
    """
    is_func_error = cleaned_df['Error(Func.)'] == 'Func.'
    df1, func_error = _split_by_mask(cleaned_df, is_func_error)

    is_background = df1['Sample_No.'].str.strip() == 'BACKGROUNDCHECK'
    df2, background = _split_by_mask(df1, is_background)
    df2 = df2.copy()  # avoid SettingWithCopyWarning on the column writes below

    has_retic = 'RET%(%)' in df2.columns

    # Reference flag columns by name rather than position (df2.columns[1:3]
    # in the original script), since RET%(%) may not be present at all.
    flag_columns = [col for col in ('RET%(%)', 'NEUT%(%)') if col in df2.columns]
    for column in flag_columns:
        df2[column] = df2[column].apply(data_convrt)

    result = {'BACKGROUND': background, 'FUNC_ERR': func_error}

    if has_retic:
        df3, retic = _split_by_mask(df2, df2['RET%(%)'] == 1)
        cbc, cbcdc = _split_by_mask(df3, df3['NEUT%(%)'] == 1)
        result['RETIC'] = retic
    else:
        # No reticulocyte channel run: split directly on the NEUT%(%) flag,
        # no separate RETIC category.
        cbc, cbcdc = _split_by_mask(df2, df2['NEUT%(%)'] == 1)

    result['CBC'] = cbc
    result['CBCDC'] = cbcdc

    return result


def data_convrt(x) -> int:
    """
    Convert a raw XN-1000 flag-column value into a 1/0 presence indicator.

    Returns 1 if the value parses as a non-negative float, or if it's a
    non-numeric string containing "---" (the instrument's placeholder for
    a flagged/error reading); returns 0 otherwise (e.g. a negative float,
    or any other non-numeric string).

    Args:
        x: The raw cell value from the RET%(%) or NEUT%(%) column.

    Returns:
        1 or 0.
    """
    try:
        y = float(x)
        if y >= 0:
            return 1
        return 0
    except (TypeError, ValueError):
        if re.search('---', str(x)):
            return 1
        return 0