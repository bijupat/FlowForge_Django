"""
Example processor: Convert an XLSX file to a PDF table.

Inputs:
    - excel_file: An XLSX file.

Output:
    - pdf_output: PDF version of the spreadsheet.
"""
import os
import logging
from typing import Dict, Any

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph

logger = logging.getLogger('flowforge.processors.convert_excel')


def process(input_files: Dict[str, str], form_data: Dict[str, Any], output_dir: str) -> Dict[str, str]:
    """
    Convert an Excel file to PDF.

    Args:
        input_files: Dict with 'excel_file' mapping to file path.
        form_data: Dict of additional form values (unused).
        output_dir: Directory for output PDF.

    Returns:
        Dict with 'pdf_output' key.
    """
    excel_path = input_files.get('excel_file') or list(input_files.values())[0]
    if not excel_path:
        raise ValueError("No input Excel file provided.")

    # Read Excel file
    try:
        df = pd.read_excel(excel_path)
        logger.info(f"Read Excel file with {len(df)} rows and {len(df.columns)} columns.")
    except Exception as e:
        logger.error(f"Failed to read Excel file: {e}")
        raise

    # Generate PDF
    output_filename = os.path.splitext(os.path.basename(excel_path))[0] + '.pdf'
    output_path = os.path.join(output_dir, output_filename)

    doc = SimpleDocTemplate(
        output_path,
        pagesize=landscape(A4) if len(df.columns) > 5 else A4,
        rightMargin=0.5 * inch,
        leftMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )

    # Prepare data for PDF table
    styles = getSampleStyleSheet()
    header_style = styles['Heading6']

    data = []
    # Header row
    header = [Paragraph(str(col), header_style) for col in df.columns]
    data.append(header)

    # Data rows
    body_style = styles['BodyText']
    for _, row in df.iterrows():
        data_row = [Paragraph(str(val) if not pd.isna(val) else '', body_style) for val in row]
        data.append(data_row)

    # Create table
    col_widths = [max(1.5 * inch, min(2.5 * inch, 6.5 * inch / len(df.columns)))] * len(df.columns)
    table = Table(data, colWidths=col_widths)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4a7cba')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f1f3f5')),
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#d3d3d3')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fa')]),
    ]))

    doc.build([table])
    logger.info(f"PDF report saved to {output_path}")

    return {
        'pdf_output': output_path,
    }
