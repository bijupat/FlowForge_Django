"""
Example processor: Patient Statistics Analysis.

Inputs:
    - patient_data: CSV file with patient records.
      Required columns: patient_id, age, gender, diagnosis, admission_date, discharge_date.

Output:
    - stats_csv: CSV with computed statistics.
    - stats_pdf: PDF summary of statistics.
"""
import os
import logging
from typing import Dict, Any

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

logger = logging.getLogger('flowforge.processors.patient_statistics')


def process(input_files: Dict[str, str], form_data: Dict[str, Any], output_dir: str) -> Dict[str, str]:
    """
    Analyze patient data and generate statistics.

    Args:
        input_files: Dict with 'patient_data' mapping to file path.
        form_data: Additional form values.
        output_dir: Output directory path.

    Returns:
        Dict with 'stats_csv' and 'stats_pdf' keys.
    """
    data_path = input_files.get('patient_data') or list(input_files.values())[0]
    if not data_path:
        raise ValueError("No patient data file provided.")

    # Read patient data
    try:
        df = pd.read_csv(data_path)
        logger.info(f"Read patient data: {len(df)} records.")
    except Exception as e:
        logger.error(f"Failed to read patient data: {e}")
        raise

    # Validate required columns
    required_cols = ['patient_id', 'age', 'gender', 'diagnosis', 'admission_date', 'discharge_date']
    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    # Convert date columns
    df['admission_date'] = pd.to_datetime(df['admission_date'], errors='coerce')
    df['discharge_date'] = pd.to_datetime(df['discharge_date'], errors='coerce')
    df['length_of_stay'] = (df['discharge_date'] - df['admission_date']).dt.days

    # Compute statistics
    total_patients = len(df)
    avg_age = df['age'].mean()
    gender_counts = df['gender'].value_counts()
    top_diagnoses = df['diagnosis'].value_counts().head(5)
    avg_los = df['length_of_stay'].mean()
    max_los = df['length_of_stay'].max()

    stats = {
        'Total Patients': total_patients,
        'Average Age': round(avg_age, 1),
        'Average Length of Stay (days)': round(avg_los, 1) if not pd.isna(avg_los) else 'N/A',
        'Max Length of Stay (days)': max_los if not pd.isna(max_los) else 'N/A',
    }

    # Output CSV
    stats_df = pd.DataFrame(list(stats.items()), columns=['Statistic', 'Value'])
    for gender, count in gender_counts.items():
        stats_df = pd.concat([stats_df, pd.DataFrame([[f'Gender: {gender}', count]], columns=['Statistic', 'Value'])])

    for diagnosis, count in top_diagnoses.items():
        stats_df = pd.concat([stats_df, pd.DataFrame([[f'Top Diagnosis: {diagnosis}', count]], columns=['Statistic', 'Value'])])

    from datetime import datetime
    date_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_path = os.path.join(output_dir, f'patient_statistics_{date_str}.csv')
    stats_df.to_csv(csv_path, index=False)
    logger.info(f"Statistics CSV saved to {csv_path}")

    # Output PDF
    pdf_path = os.path.join(output_dir, f'patient_statistics_report_{date_str}.pdf')
    _generate_pdf_report(stats_df, pdf_path)
    logger.info(f"Statistics PDF saved to {pdf_path}")

    return {
        'stats_csv': csv_path,
        'stats_pdf': pdf_path,
    }


def _generate_pdf_report(stats_df: pd.DataFrame, pdf_path: str):
    """
    Generate a PDF report from the statistics DataFrame.

    Args:
        stats_df: DataFrame with columns 'Statistic' and 'Value'.
        pdf_path: Output PDF path.
    """
    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=A4,
        rightMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = styles['Title']
    heading_style = styles['Heading2']
    body_style = styles['BodyText']

    elements = []

    # Title
    elements.append(Paragraph("Patient Statistics Report", title_style))
    elements.append(Spacer(1, 0.25 * inch))

    # Date
    elements.append(Paragraph(f"Generated: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}", body_style))
    elements.append(Spacer(1, 0.25 * inch))

    # Table
    table_data = []
    header_style = styles['Heading6']
    header = [Paragraph('Statistic', header_style), Paragraph('Value', header_style)]
    table_data.append(header)

    for _, row in stats_df.iterrows():
        table_data.append([
            Paragraph(str(row['Statistic']), body_style),
            Paragraph(str(row['Value']), body_style),
        ])

    table = Table(table_data, colWidths=[3.5 * inch, 2.5 * inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4a7cba')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f8f9fa')),
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#dee2e6')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f1f3f5')]),
    ]))

    elements.append(table)

    doc.build(elements)
