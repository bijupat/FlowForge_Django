"""
QC Levey-Jennings control chart processor.

Reads Abbott Alinity-style QC export CSVs, groups results by
(Assay, Control Name, Control Level), and for each group produces a
Levey-Jennings-style control chart: raw QC results plotted over time,
with statistically-derived mean/+-1SD/+-2SD/+-3SD reference lines.

A group's reference limits are computed per "segment" - a run of
consecutive results sharing the same raw CONTROL RANGE value. Whenever
CONTROL RANGE changes (e.g. a new control lot), a new segment starts
with its own independently-computed mean/SD, rather than blending
statistics across a lot change. A segment with only one result has no
meaningful SD; that segment's reference "lines" fall back to the
CONTROL RANGE's low/high bounds instead, shown as distinctly-colored
markers (there being only one point, no line can be drawn).

FlowForge entrypoint: `process(input_files, form_data, output_dir)`.
"""
import re
from typing import Dict, List, Tuple, Union

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import ScatterChart, Series, Reference
from openpyxl.chart.marker import Marker
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from apps.processors.common import (
    build_output_filename,
    normalize_paths,
    read_and_concat_csvs,
    wrap_processing_errors,
)
from apps.workflows.services import ProcessingError

# Alternate raw column names seen across different Alinity export layouts
# (see apps/processors/alinity.py, which established this same pattern).
COLUMN_ALIASES: Dict[str, List[str]] = {
    'Assay_Name': ['ASSAY'],
    'Control_Name': ['CONTROL_NAME'],
    'Control_Level': ['CONTROL_LEVEL'],
    'Control_Lot': ['CONTROL_LOT'],
}
MERGED_DATETIME_ALIASES = ['DATE/TIME_COMPLETED']

# Columns written to the Data sheet, in order. Everything from
# Result_Value_Squared onward is a formula column computed inside Excel
# itself (not a Python-computed value), per this environment's
# requirement that generated workbooks recalculate from their own data
# rather than ship hardcoded results.
HEADERS = [
    'Assay', 'Control_Name', 'Control_Level', 'Control_Lot', 'DateTime',
    'Result_Raw', 'Result_Value', 'Result_Value_Squared', 'Result_Unit',
    'Control_Range_Raw', 'Range_Low', 'Range_High', 'Range_Quality',
    'Segment_Id', 'Segment_N', 'Segment_Mean', 'Segment_SumSq', 'Segment_SD',
    'Limit_Type', 'Stat_Center', 'Stat_Upper1', 'Stat_Lower1', 'Stat_Upper2',
    'Stat_Lower2', 'Stat_Upper3', 'Stat_Lower3',
    'RangeFallback_Center', 'RangeFallback_Upper', 'RangeFallback_Lower',
]
COL = {name: i + 1 for i, name in enumerate(HEADERS)}

CHART_HEIGHT_ROWS = 16

# (column, legend label, color, line width in points, marker symbol, marker size)
# marker_symbol='none' -> drawn as a line (segment has >=2 points).
# Any other symbol -> drawn as markers only, no line (segment has exactly
# 1 point, so there is no line length to render).
STAT_TIERS = [
    ('Stat_Center', 'Mean', '000000', 2.5, 'none', 0),
    ('Stat_Upper1', '+1 SD', '2CA02C', 1.25, 'none', 0),
    ('Stat_Lower1', '-1 SD', '2CA02C', 1.25, 'none', 0),
    ('Stat_Upper2', '+2 SD', 'FF7F0E', 1.25, 'none', 0),
    ('Stat_Lower2', '-2 SD', 'FF7F0E', 1.25, 'none', 0),
    ('Stat_Upper3', '+3 SD', 'D62728', 1.25, 'none', 0),
    ('Stat_Lower3', '-3 SD', 'D62728', 1.25, 'none', 0),
]
RANGE_TIERS = [
    ('RangeFallback_Center', 'Midpoint (n=1, range-based)', '9467BD', 1.5, 'diamond', 9),
    ('RangeFallback_Upper', 'Upper (n=1, range-based)', '9467BD', 1.0, 'triangle', 8),
    ('RangeFallback_Lower', 'Lower (n=1, range-based)', '9467BD', 1.0, 'triangle', 8),
]


def process(input_files: Dict[str, Union[str, List[str]]], form_data: dict, output_dir: str) -> dict:
    """
    Process one or more QC export CSVs into a Levey-Jennings control
    chart workbook: a "Data" sheet (raw + Excel-formula-derived per-segment
    statistics) and a "Charts" sheet (one chart per Assay+Control_Name+
    Control_Level group).

    Args:
        input_files: Mapping of form field names to temp file path(s).
            Expected key:
                - 'qc_files': path or list of paths to QC export CSVs.
        form_data: Any other form field values submitted with the workflow.
            An optional 'batch_label' key, if present and non-empty, is
            sanitized and included in the generated output filename.
        output_dir: Directory (already created by WorkflowProcessorService)
            that the generated report must be written into.

    Returns:
        A dict mapping the output key to the generated file path, e.g.
        {'lj_report': '/path/to/output_dir/out_QCLJ_Nov_2025.xlsx'}.

    Raises:
        ProcessingError: If required files are missing, malformed, or
            processing otherwise fails. Raised with a user-friendly message.
    """
    file_paths = normalize_paths(input_files.get('qc_files'))
    if not file_paths:
        raise ProcessingError('No QC CSV files were uploaded.')

    with wrap_processing_errors('QC Levey-Jennings'):
        df = _load_and_prepare(file_paths)

    if df.empty:
        raise ProcessingError('No usable QC rows were found in the uploaded file(s).')

    start_time = df['DateTime'].iloc[0]
    output_filename = build_output_filename('QCLJ', form_data, start_time, extension='xlsx')
    output_path = f'{output_dir}/{output_filename}'

    with wrap_processing_errors('QC Levey-Jennings'):
        _write_workbook(df, output_path)

    return {'lj_report': output_path}


def _canonicalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename known alternate raw column names to canonical ones (see COLUMN_ALIASES)."""
    rename_map = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        if canonical in df.columns:
            continue
        for alias in aliases:
            if alias in df.columns:
                rename_map[alias] = canonical
                break
    return df.rename(columns=rename_map) if rename_map else df


def _resolve_datetime(df: pd.DataFrame) -> pd.Series:
    """Resolve the completion date/time, supporting either a split or merged column layout."""
    if 'Date_of_Completion' in df.columns and 'Time_of_Completion' in df.columns:
        combined = df['Date_of_Completion'].astype(str) + ' ' + df['Time_of_Completion'].astype(str)
        return pd.to_datetime(combined, format='%d.%m.%Y %H:%M')
    for alias in MERGED_DATETIME_ALIASES:
        if alias in df.columns:
            return pd.to_datetime(df[alias].astype(str).str.strip(), format='%d.%m.%Y %H:%M')
    raise KeyError("'Date_of_Completion'/'Time_of_Completion' (or a merged completion date/time column)")


def _parse_result(raw) -> Tuple[Union[float, None], Union[str, None]]:
    """
    Parse a raw RESULT string like '31 U/L' or '> 20.00 pg/mL' into
    (numeric_value, unit). A leading '<'/'>' (censored result) is
    stripped; the numeric value itself is kept as-is.
    """
    m = re.match(r'^[<>]?\s*(-?\d+\.?\d*)\s*(.*)$', str(raw).strip())
    if not m:
        return None, None
    return float(m.group(1)), m.group(2).strip()


def _parse_range(raw) -> Tuple[Union[float, None], Union[float, None]]:
    """Parse a 'low - high' CONTROL RANGE string into (low, high). Returns (None, None) if unparseable."""
    m = re.match(r'^(-?\d+\.?\d*)\s*-\s*(-?\d+\.?\d*)$', str(raw).strip())
    if not m:
        return None, None
    return float(m.group(1)), float(m.group(2))


def _assign_segments(group: pd.DataFrame) -> pd.Series:
    """
    Assign a 1-based Segment_Id within a single (Assay, Control_Name,
    Control_Level) group, incrementing whenever Control_Range_Raw differs
    from the immediately preceding row. Assumes group is already sorted
    chronologically by DateTime.
    """
    seg_id = 1
    ids = [seg_id]
    prev_range = group['Control_Range_Raw'].iloc[0]
    for i in range(1, len(group)):
        cur_range = group['Control_Range_Raw'].iloc[i]
        if cur_range != prev_range:
            seg_id += 1
        ids.append(seg_id)
        prev_range = cur_range
    return pd.Series(ids, index=group.index)


def _load_and_prepare(file_paths: List[str]) -> pd.DataFrame:
    """
    Read, clean, parse, and segment a group of QC export CSVs.

    Returns:
        A DataFrame sorted by (Assay_Name, Control_Name, Control_Level,
        DateTime), with Result_Value/Unit, Range_Low/High, Range_Quality,
        and Segment_Id columns added.
    """
    df = read_and_concat_csvs(file_paths)
    df = _canonicalize_columns(df)
    df['DateTime'] = _resolve_datetime(df)
    df['Control_Level'] = df['Control_Level'].astype(str).str.strip().str.title()

    parsed = df['Result'].apply(_parse_result)
    df['Result_Value'] = parsed.apply(lambda x: x[0])
    df['Result_Unit'] = parsed.apply(lambda x: x[1])

    df['Control_Range_Raw'] = df['Control_Range'].astype(str).str.strip()
    rparsed = df['Control_Range_Raw'].apply(_parse_range)
    df['Range_Low'] = rparsed.apply(lambda x: x[0])
    df['Range_High'] = rparsed.apply(lambda x: x[1])
    df['Range_Quality'] = df['Range_Low'].apply(
        lambda v: 'OK' if pd.notna(v) else 'Unparseable - verify original export'
    )

    df = df.dropna(subset=['Result_Value'])
    df = df.sort_values(['Assay_Name', 'Control_Name', 'Control_Level', 'DateTime'], ignore_index=True)
    df['Segment_Id'] = df.groupby(
        ['Assay_Name', 'Control_Name', 'Control_Level'], group_keys=False
    ).apply(_assign_segments)

    return df


def _write_workbook(df: pd.DataFrame, output_path: str) -> None:
    """Build and save the two-sheet workbook (Data + Charts) for the prepared DataFrame."""
    wb = Workbook()
    data_ws = wb.active
    data_ws.title = 'Data'
    last_row = _write_data_sheet(data_ws, df)

    charts_ws = wb.create_sheet('Charts', 0)
    _write_charts_sheet(charts_ws, data_ws, df)

    for col_idx in range(1, len(HEADERS) + 1):
        data_ws.column_dimensions[get_column_letter(col_idx)].width = 16

    wb.save(output_path)


def _write_data_sheet(ws, df: pd.DataFrame) -> int:
    """
    Write the Data sheet: raw values plus Excel-formula-driven per-segment
    statistics (COUNTIFS/AVERAGEIFS/SUMIFS/IF/SQRT only - no post-2007
    functions, so these evaluate identically in Excel and LibreOffice).

    Returns:
        The 1-indexed row number of the last data row (for building
        chart references afterward).
    """
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    last_row = len(df) + 1

    def rng(col_name: str) -> str:
        c = get_column_letter(COL[col_name])
        return f'${c}$2:${c}${last_row}'

    for i, row in enumerate(df.itertuples(index=False), start=2):
        ws.cell(i, COL['Assay'], row.Assay_Name)
        ws.cell(i, COL['Control_Name'], row.Control_Name)
        ws.cell(i, COL['Control_Level'], row.Control_Level)
        ws.cell(i, COL['Control_Lot'], row.Control_Lot)
        dt_cell = ws.cell(i, COL['DateTime'], row.DateTime)
        dt_cell.number_format = 'yyyy-mm-dd hh:mm'
        ws.cell(i, COL['Result_Raw'], row.Result)
        ws.cell(i, COL['Result_Value'], row.Result_Value)
        ws.cell(i, COL['Result_Value_Squared'], f'={get_column_letter(COL["Result_Value"])}{i}^2')
        ws.cell(i, COL['Result_Unit'], row.Result_Unit)
        ws.cell(i, COL['Control_Range_Raw'], row.Control_Range_Raw)
        ws.cell(i, COL['Range_Low'], row.Range_Low)
        ws.cell(i, COL['Range_High'], row.Range_High)
        rq_cell = ws.cell(i, COL['Range_Quality'], row.Range_Quality)
        if row.Range_Quality != 'OK':
            rq_cell.fill = PatternFill(start_color='FFF3CD', end_color='FFF3CD', fill_type='solid')
        ws.cell(i, COL['Segment_Id'], row.Segment_Id)

        a = get_column_letter(COL['Assay'])
        b = get_column_letter(COL['Control_Name'])
        c = get_column_letter(COL['Control_Level'])
        seg = get_column_letter(COL['Segment_Id'])
        crit = (
            f'{rng("Assay")},{a}{i},'
            f'{rng("Control_Name")},{b}{i},'
            f'{rng("Control_Level")},{c}{i},'
            f'{rng("Segment_Id")},{seg}{i}'
        )
        ws.cell(i, COL['Segment_N'], f'=COUNTIFS({crit})')
        ws.cell(i, COL['Segment_Mean'], f'=AVERAGEIFS({rng("Result_Value")},{crit})')
        ws.cell(i, COL['Segment_SumSq'], f'=SUMIFS({rng("Result_Value_Squared")},{crit})')

        n_c = f'{get_column_letter(COL["Segment_N"])}{i}'
        mean_c = f'{get_column_letter(COL["Segment_Mean"])}{i}'
        sumsq_c = f'{get_column_letter(COL["Segment_SumSq"])}{i}'
        sd_c = f'{get_column_letter(COL["Segment_SD"])}{i}'
        rq_c = f'{get_column_letter(COL["Range_Quality"])}{i}'
        low_c = f'{get_column_letter(COL["Range_Low"])}{i}'
        high_c = f'{get_column_letter(COL["Range_High"])}{i}'

        # Sample variance via the computational formula (Sumsq - Mean^2*N)/(N-1),
        # avoiding array/CSE formulas entirely since there is no native
        # STDEVIFS in Excel or LibreOffice.
        ws.cell(i, COL['Segment_SD'], f'=IF({n_c}<2,"",SQRT(({sumsq_c}-({mean_c}^2)*{n_c})/({n_c}-1)))')
        ws.cell(i, COL['Limit_Type'], f'=IF({n_c}>=2,"Statistical (\u00b1SD)","Range-based (n=1)")')
        ws.cell(i, COL['Stat_Center'], f'=IF({n_c}<2,"",{mean_c})')
        ws.cell(i, COL['Stat_Upper1'], f'=IF({n_c}<2,"",{mean_c}+{sd_c})')
        ws.cell(i, COL['Stat_Lower1'], f'=IF({n_c}<2,"",{mean_c}-{sd_c})')
        ws.cell(i, COL['Stat_Upper2'], f'=IF({n_c}<2,"",{mean_c}+2*{sd_c})')
        ws.cell(i, COL['Stat_Lower2'], f'=IF({n_c}<2,"",{mean_c}-2*{sd_c})')
        ws.cell(i, COL['Stat_Upper3'], f'=IF({n_c}<2,"",{mean_c}+3*{sd_c})')
        ws.cell(i, COL['Stat_Lower3'], f'=IF({n_c}<2,"",{mean_c}-3*{sd_c})')
        # Only ever populated when this row's own segment has fewer than 2
        # points AND its range parsed cleanly - otherwise left blank ("")
        # rather than silently computing a misleading number from missing data.
        ws.cell(i, COL['RangeFallback_Center'], f'=IF(OR({n_c}>=2,{rq_c}<>"OK"),"",({low_c}+{high_c})/2)')
        ws.cell(i, COL['RangeFallback_Upper'], f'=IF(OR({n_c}>=2,{rq_c}<>"OK"),"",{high_c})')
        ws.cell(i, COL['RangeFallback_Lower'], f'=IF(OR({n_c}>=2,{rq_c}<>"OK"),"",{low_c})')

    ws.freeze_panes = 'A2'
    return last_row


def _compute_y_axis_bounds(gdf: pd.DataFrame) -> Tuple[float, float]:
    """
    Compute a padded Y-axis range for one group's chart, based on that
    group's actual Result_Value data and each of its segments' plotted
    reference-line extents (mean +/- 3SD, or the CONTROL RANGE bounds for
    a single-point segment).

    This is purely a chart display setting, computed here in Python only
    to decide axis scaling - the underlying Data sheet's mean/SD cells
    remain fully Excel-formula-driven regardless, so this doesn't
    duplicate or bypass any reported value.

    Args:
        gdf: The rows for a single (Assay, Control_Name, Control_Level)
            group (a slice of the full prepared DataFrame).

    Returns:
        (axis_min, axis_max), padded by ~10% of the overall value span
        (or a small absolute margin if every relevant value is identical).
    """
    values = [gdf['Result_Value'].min(), gdf['Result_Value'].max()]

    for _, sdf in gdf.groupby('Segment_Id'):
        if len(sdf) >= 2:
            mean = sdf['Result_Value'].mean()
            sd = sdf['Result_Value'].std(ddof=1)
            if pd.notna(sd):
                values.append(mean - 3 * sd)
                values.append(mean + 3 * sd)
        else:
            row = sdf.iloc[0]
            if row['Range_Quality'] == 'OK':
                values.append(row['Range_Low'])
                values.append(row['Range_High'])

    values = [v for v in values if pd.notna(v)]
    lo, hi = min(values), max(values)
    span = hi - lo
    pad = span * 0.1 if span > 0 else max(abs(hi) * 0.1, 1.0)
    return lo - pad, hi + pad


def _write_charts_sheet(charts_ws, data_ws, df: pd.DataFrame) -> None:
    """
    Add one Levey-Jennings chart per (Assay, Control_Name, Control_Level)
    group to charts_ws, with each chart's series referencing cells on
    data_ws directly.

    Since the Data sheet is sorted by group then by segment then
    chronologically, each group - and each segment within it - occupies a
    contiguous row block. Each segment's reference-line series is scoped
    to just its own row range rather than the whole group's range, so
    limit lines correctly break at segment boundaries without needing any
    blank-cell or #N/A gap trick (which would otherwise trip this
    environment's zero-formula-error requirement).

    Note: chart series must be built from Reference objects constructed
    directly against data_ws (not charts_ws with some other sheet name
    attached afterward) - openpyxl bakes the sheet name into a Reference
    from the worksheet object passed at construction time, so referencing
    the wrong worksheet object here silently produces charts that point
    at blank cells on charts_ws instead of the real data on data_ws.
    """
    df = df.reset_index(drop=True)
    df['_excel_row'] = df.index + 2  # +1 for header, +1 for 1-indexing

    anchor = 1
    group_cols = ['Assay_Name', 'Control_Name', 'Control_Level']
    for (assay, cname, level), gdf in df.groupby(group_cols, sort=False):
        g_start = int(gdf['_excel_row'].min())
        g_end = int(gdf['_excel_row'].max())
        segments = []
        for seg_id, sdf in gdf.groupby('Segment_Id', sort=True):
            s_start = int(sdf['_excel_row'].min())
            s_end = int(sdf['_excel_row'].max())
            seg_n = len(sdf)
            range_ok = bool(sdf['Range_Quality'].iloc[0] == 'OK')
            segments.append((s_start, s_end, seg_n, range_ok))
        label = f'{assay} | {cname} | {level}'
        y_min, y_max = _compute_y_axis_bounds(gdf)
        anchor = _add_group_chart(charts_ws, data_ws, anchor, label, g_start, g_end, segments, y_min, y_max)


def _add_group_chart(
    charts_ws, data_ws, anchor_row: int, group_label: str,
    group_start: int, group_end: int, segments: List[Tuple[int, int, int, bool]],
    y_min: float, y_max: float,
) -> int:
    """
    Build and place one Levey-Jennings chart for a single group.

    Args:
        charts_ws: The worksheet to place the chart on.
        data_ws: The actual Data worksheet object each series reads from -
            must be the real worksheet, not just a sheet name string,
            since that's what openpyxl uses to build each series' cell
            reference formula.
        anchor_row: Row on charts_ws to anchor this chart's top-left corner.
        group_label: Chart title.
        group_start, group_end: 1-indexed Data-sheet row range for this
            group's Result_Value/DateTime series (spans all segments).
        segments: List of (seg_start_row, seg_end_row, seg_n, range_ok)
            tuples, one per segment in this group, in row order.
        y_min, y_max: Explicit Y-axis bounds for this chart, so each
            chart uses the vertical space available to it rather than a
            shared/default scale that leaves most of the chart empty for
            a group whose values cluster tightly.

    Returns:
        The anchor row the *next* chart should use.
    """
    chart = ScatterChart()
    chart.title = group_label
    chart.style = 2
    chart.x_axis.title = 'Date'
    chart.y_axis.title = 'Result'
    chart.x_axis.number_format = 'yyyy-mm-dd'
    chart.y_axis.scaling.min = y_min
    chart.y_axis.scaling.max = y_max
    chart.height = 8
    chart.width = 24

    dates_ref = Reference(data_ws, min_col=COL['DateTime'], min_row=group_start, max_row=group_end)

    result_ref = Reference(data_ws, min_col=COL['Result_Value'], min_row=group_start, max_row=group_end)
    result_series = Series(result_ref, dates_ref, title='Result')
    result_series.marker = Marker(symbol='circle', size=6)
    result_series.graphicalProperties.line.width = 12000
    result_series.graphicalProperties.line.solidFill = '1F77B4'
    result_series.marker.graphicalProperties.solidFill = '1F77B4'
    chart.series.append(result_series)

    for seg_start, seg_end, seg_n, range_ok in segments:
        seg_dates_ref = Reference(data_ws, min_col=COL['DateTime'], min_row=seg_start, max_row=seg_end)
        tiers = STAT_TIERS if seg_n >= 2 else (RANGE_TIERS if range_ok else [])
        for col_name, label, color, width, marker_symbol, marker_size in tiers:
            val_ref = Reference(data_ws, min_col=COL[col_name], min_row=seg_start, max_row=seg_end)
            s = Series(val_ref, seg_dates_ref, title=label)
            if marker_symbol == 'none':
                s.marker = Marker(symbol='none')
                s.graphicalProperties.line.width = int(width * 12700)
                s.graphicalProperties.line.solidFill = color
                if 'Mean' not in label:
                    s.graphicalProperties.line.dashStyle = 'dash'
            else:
                # A 1-point segment has no line length to render - show a
                # visible marker at the range bound instead.
                s.marker = Marker(symbol=marker_symbol, size=marker_size)
                s.marker.graphicalProperties.solidFill = color
                s.marker.graphicalProperties.line.solidFill = color
                s.graphicalProperties.line.noFill = True
            chart.series.append(s)

    charts_ws.add_chart(chart, f'A{anchor_row}')
    return anchor_row + CHART_HEIGHT_ROWS