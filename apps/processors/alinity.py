"""
Alinity QC/Sample processor.

Reads Abbott Alinity Sample and QC (control) CSV exports, cleans and
aggregates them by assay, and writes a combined summary + detail CSV.

Exposes a FlowForge-compatible `process(input_files, form_data)` entrypoint
in addition to the original standalone CLI (`python alinity.py`).
At bottom of file, Alinity Counts workflow database parameters are listed.
"""
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Union

import pandas as pd

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
            Not currently used by this processor.
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
    sample_paths = _normalize_paths(input_files.get('sample_files'))
    qc_paths = _normalize_paths(input_files.get('qc_files'))

    if not sample_paths:
        raise ProcessingError('No Sample files were uploaded.')
    if not qc_paths:
        raise ProcessingError('No QC (control) files were uploaded.')

    try:
        assaywise_df, tests_count = _build_assay_summary(
            sample_paths, drop_columns=['Operator_ID', 'Time']
        )
        assaywise_df_c, tests_count_c = _build_assay_summary(
            qc_paths,
            drop_columns=['Operator_ID', 'Time', 'Control_Name', 'Control_Level', 'Control_Lot'],
        )
    except KeyError as exc:
        raise ProcessingError(
            f'Uploaded file is missing an expected column: {exc}. '
            'Please check the file matches the Alinity export format.'
        ) from exc
    except ValueError as exc:
        raise ProcessingError(
            f'Could not parse date/time values in the uploaded file: {exc}'
        ) from exc
    except Exception as exc:  # noqa: BLE001 - surface any other failure cleanly
        raise ProcessingError(f'Failed to process Alinity files: {exc}') from exc

    start_time = assaywise_df['Time'].iloc[0]

    output_filename = f"out_alinity_{start_time.strftime('%b')}_{start_time.strftime('%Y')}.csv"
    output_path = os.path.join(output_dir, output_filename)

    # Write summary counts first, then detailed assay-wise rows for both groups.
    tests_count.to_csv(output_path, mode='w')
    tests_count_c.to_csv(output_path, mode='a')
    assaywise_df.to_csv(output_path, mode='a', index=False)
    assaywise_df_c.to_csv(output_path, mode='a', index=False)

    return {'alinity_report': output_path}


def _normalize_paths(value: Union[str, List[str], None]) -> List[str]:
    """
    Normalize a single path, list of paths, or None into a list of strings.

    Args:
        value: A file path, list of file paths, or None.

    Returns:
        A list of file path strings (empty if value was falsy).
    """
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(path) for path in value]
    return [str(value)]


def _build_assay_summary(
    file_paths: List[str], drop_columns: List[str]
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Read, clean, and aggregate a group of Alinity CSV files by assay.

    Args:
        file_paths: CSV file paths to concatenate and clean.
        drop_columns: Columns to exclude before computing per-assay counts
            (e.g. non-QC files drop fewer columns than QC files).

    Returns:
        Tuple of (assaywise_df, tests_count):
            - assaywise_df: cleaned, sorted rows with a combined Time column.
            - tests_count: per-assay row counts after dropping drop_columns.
    """
    dfs = (pd.read_csv(path, low_memory=False) for path in file_paths)
    df = pd.concat(dfs, ignore_index=True)
    df.columns = df.columns.str.strip().str.replace(' ', '_')

    cleaned_df = df.filter(items=COLUMNS_NEEDED)
    cleaned_df['Time'] = (
        cleaned_df['Date_of_Completion'].astype(str) + ' ' + cleaned_df['Time_of_Completion']
    )
    cleaned_df['Time'] = pd.to_datetime(cleaned_df['Time'], format='%d.%m.%Y %H:%M')
    cleaned_df.drop(columns=['Date_of_Completion', 'Time_of_Completion'], inplace=True)

    assaywise_df = cleaned_df.sort_values(by=['Assay_Name', 'Time'], ignore_index=True)
    tests_count = assaywise_df.drop(columns=drop_columns).groupby('Assay_Name').count()

    return assaywise_df, tests_count


# ---------------------------------------------------------------------------
# Legacy standalone CLI usage (unchanged behavior): `python alinity.py`
# ---------------------------------------------------------------------------

def main():
    qcs, samples = get_files_from_dir(input("Enter Root folder Name : "))
    print("QC Files : ")
    for qc in qcs:
        print(qc)
    print("Sample Files : ")
    for sample in samples:
        print(sample)

    assaywise_df, tests_count = _build_assay_summary(
        samples, drop_columns=['Operator_ID', 'Time']
    )
    assaywise_df_c, tests_count_c = _build_assay_summary(
        qcs,
        drop_columns=['Operator_ID', 'Time', 'Control_Name', 'Control_Level', 'Control_Lot'],
    )

    start_time = assaywise_df["Time"][0]
    end_time = assaywise_df.iloc[-1]['Time']
    print(f'Alinity test datas from {start_time} to {end_time}')

    output_path = f'./{start_time.strftime("%b")}{start_time.strftime("%Y")}/out_alinity_{start_time.strftime("%b")}_{start_time.strftime("%Y")}.csv'
    output_dir = os.path.dirname(output_path)
    os.makedirs(output_dir, exist_ok=True)

    tests_count.to_csv(output_path, mode='a')
    tests_count_c.to_csv(output_path, mode='a')
    assaywise_df.to_csv(output_path, mode='a', index=False)
    assaywise_df_c.to_csv(output_path, mode='a', index=False)
    print(tests_count)
    print(tests_count_c)


def get_files_from_dir(dir):
    path = Path(os.path.join(os.getcwd(), dir, "alinity", "qc"))
    qcs = list(path.glob("*.csv"))
    path = Path(os.path.join(os.getcwd(), dir, "alinity", "sample"))
    samples = list(path.glob("*.csv"))
    if not qcs:
        sys.exit("QC files not found !!")
    elif not samples:
        sys.exit("Sample files not found !!")
    else:
        return qcs, samples


if __name__ == "__main__":
    main()


"""
in database in workflows/workflow table add following if new database
Name : Alinity Counts
Description : Test and QC counts and details for Alinity
Form Configuration : [{"name": "sample_files", "label": "Sample CSV Files", "order": 1, "multiple": true, "required": true, "help_text": "Upload one or more Alinity Sample export CSVs (hold Ctrl/Cmd to select several).", "field_type": "file", "max_size_mb": 50, "allowed_extensions": ["csv"]}, {"name": "qc_files", "label": "QC (Control) CSV Files", "order": 2, "multiple": true, "required": true, "help_text": "Upload one or more Alinity QC/control export CSVs (hold Ctrl/Cmd to select several).", "field_type": "file", "max_size_mb": 50, "allowed_extensions": ["csv"]}]
Processor module:processors.alinity
Processor function:process
Output configuration: [{"name": "alinity_report", "label": "Alinity Assay Summary Report", "format": "csv"}]
"""