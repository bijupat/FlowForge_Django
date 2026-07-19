"""
Shared utilities for FlowForge processor modules (apps/processors/*.py).

Extracts patterns repeated across processors: normalizing form-field file
path(s), reading+concatenating CSVs with cleaned column headers, wrapping
a processor's data-cleaning logic so common failure types surface as a
user-friendly ProcessingError instead of a raw traceback, and sanitizing
an optional user-supplied label for inclusion in output filenames.
"""
import re
from contextlib import contextmanager
from typing import Iterator, List, Optional, Union

import pandas as pd

from apps.workflows.services import ProcessingError


def normalize_paths(value: Union[str, List[str], None]) -> List[str]:
    """
    Normalize a single path, list of paths, or None into a list of strings.

    Every FlowForge file input field's value in `input_files` is either a
    single path string (a regular file field) or a list of path strings
    (a field configured with "multiple": true) - this makes processor code
    that needs to handle both cases uniformly.

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


def read_and_concat_csvs(file_paths: List[str]) -> pd.DataFrame:
    """
    Read one or more CSV files and concatenate them into a single
    DataFrame, with column headers normalized (whitespace stripped,
    internal spaces replaced with underscores).

    Args:
        file_paths: CSV file paths to read and concatenate.

    Returns:
        The concatenated DataFrame with cleaned column headers. Row order
        follows file_paths order; no other cleaning or filtering is done -
        callers still handle their own column selection, dtype casting,
        etc.
    """
    dfs = (pd.read_csv(path, low_memory=False) for path in file_paths)
    df = pd.concat(dfs, ignore_index=True)
    df.columns = df.columns.str.strip().str.replace(' ', '_')
    return df


@contextmanager
def wrap_processing_errors(processor_name: str) -> Iterator[None]:
    """
    Context manager that converts common data-cleaning failures into a
    user-friendly ProcessingError, so callers don't each repeat the same
    try/except KeyError/ValueError/Exception block.

    Args:
        processor_name: Human-readable name used in generated messages,
            e.g. "Alinity" or "XN-1000".

    Yields:
        None. Wrap the file-reading/cleaning/classifying calls in a
        `with wrap_processing_errors('MyProcessor'):` block.

    Raises:
        ProcessingError: Always, if the wrapped block raises KeyError,
            ValueError, or any other Exception. A ProcessingError raised
            directly inside the block (e.g. for a missing upload) passes
            through unchanged rather than being re-wrapped.
    """
    try:
        yield
    except ProcessingError:
        raise
    except KeyError as exc:
        raise ProcessingError(
            f'Uploaded file is missing an expected column: {exc}. '
            f'Please check the file matches the {processor_name} export format.'
        ) from exc
    except ValueError as exc:
        raise ProcessingError(
            f'Could not parse date/time values in the uploaded file: {exc}'
        ) from exc
    except Exception as exc:  # noqa: BLE001 - surface any other failure cleanly
        raise ProcessingError(f'Failed to process {processor_name} files: {exc}') from exc


def build_output_filename(prefix: str, form_data: dict, timestamp, extension: str = 'csv') -> str:
    """
    Build an output filename, including an optional user-supplied label
    from form_data so that runs on different input files (in the same
    month) don't all produce an identically-named download.

    Args:
        prefix: Processor-specific filename prefix, e.g. "alinity" or
            "XN1000".
        form_data: The workflow's non-file form field values. Looks for a
            "batch_label" key (from an optional text field in the
            workflow's input_config); any other keys are ignored here.
        timestamp: A pandas/datetime Timestamp used for the month/year
            portion of the filename (typically the first row's parsed
            date from the uploaded file).
        extension: File extension without the leading dot. Defaults to
            "csv" since every current processor writes CSV output.

    Returns:
        A filename string, e.g. "out_alinity_SiteA_Jul_2026.csv" if a
        label was supplied, or "out_alinity_Jul_2026.csv" if not.
    """
    label = _sanitize_filename_label(form_data.get('batch_label') if form_data else None)
    label_part = f'{label}_' if label else ''
    return f"out_{prefix}_{label_part}{timestamp.strftime('%b')}_{timestamp.strftime('%Y')}.{extension}"


def _sanitize_filename_label(value: Optional[str]) -> str:
    """
    Sanitize a user-supplied label for safe use inside a filename.

    Strips leading/trailing whitespace, then replaces any run of
    characters that aren't letters, digits, hyphens, or underscores with
    a single underscore - so spaces, slashes, and other characters that
    would be awkward or unsafe in a filename don't cause problems.

    Args:
        value: The raw form field value, or None if the field was empty.

    Returns:
        A sanitized label string, or an empty string if value was falsy.
    """
    if not value:
        return ''
    return re.sub(r'[^A-Za-z0-9_-]+', '_', str(value).strip()).strip('_')