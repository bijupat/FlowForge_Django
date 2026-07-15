"""
Sysmex XN-1000 CBC/DIFF processor.

Reads Sysmex XN-1000 export CSVs, cleans them, and splits samples into
five test categories (CBC, CBCDC, RETIC, BACKGROUND, FUNC_ERR), writing a
combined summary + detail CSV.

Exposes a FlowForge-compatible `process(input_files, form_data, output_dir)`
entrypoint in addition to the original standalone CLI (`python xn.py`).
"""
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Union

import pandas as pd

from apps.workflows.services import ProcessingError

# Columns retained from the raw XN-1000 export before any processing.
COLUMNS_NEEDED = ['Sample_No.', 'Date', 'Time', 'RET%(%)', 'NEUT%(%)', 'Error(Func.)']
FINAL_COLUMNS_NEEDED = ['Sample_No.', 'DateTime', 'Test']


def process(input_files: Dict[str, Union[str, List[str]]], form_data: dict, output_dir: str) -> dict:
    """
    Process one or more Sysmex XN-1000 export CSVs into a classified
    summary report (CBC, CBCDC, RETIC, BACKGROUND, FUNC_ERR).

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
            Not currently used by this processor.
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
    file_paths = _normalize_paths(input_files.get('xn_files'))

    if not file_paths:
        raise ProcessingError('No XN-1000 CSV files were uploaded.')

    try:
        cleaned_df = _load_and_clean(file_paths)
        final_dfs = _classify_samples(cleaned_df)
    except KeyError as exc:
        raise ProcessingError(
            f'Uploaded file is missing an expected column: {exc}. '
            'Please check the file matches the XN-1000 export format.'
        ) from exc
    except ValueError as exc:
        raise ProcessingError(
            f'Could not parse date/time values in the uploaded file: {exc}'
        ) from exc
    except Exception as exc:  # noqa: BLE001 - surface any other failure cleanly
        raise ProcessingError(f'Failed to process XN-1000 files: {exc}') from exc

    if cleaned_df.empty:
        raise ProcessingError('No valid sample rows remained after cleaning the uploaded files.')

    start_time = cleaned_df['DateTime'].iloc[0]
    output_filename = f"out_XN1000_{start_time.strftime('%b')}_{start_time.strftime('%Y')}.csv"
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


def _load_and_clean(file_paths: List[str]) -> pd.DataFrame:
    """
    Read, concatenate, and clean a group of XN-1000 CSV files.

    Args:
        file_paths: CSV file paths to concatenate and clean.

    Returns:
        A cleaned, sorted DataFrame with a combined DateTime column, rows
        containing 'ERR' in Sample_No. removed.
    """
    dfs = (pd.read_csv(path, low_memory=False) for path in file_paths)
    df = pd.concat(dfs, ignore_index=True)
    df.columns = df.columns.str.strip().str.replace(' ', '_')

    cleaned_df = df.filter(items=COLUMNS_NEEDED)
    cleaned_df['DateTime'] = cleaned_df['Date'].astype(str) + ' ' + cleaned_df['Time']
    cleaned_df['DateTime'] = pd.to_datetime(cleaned_df['DateTime'], format='%d-%m-%Y %H:%M:%S')
    cleaned_df = cleaned_df.drop(columns=['Date', 'Time'])

    # Drop error samples. na=False guards against a NaN Sample_No. raising
    # instead of just being treated as "doesn't contain ERR".
    cleaned_df = cleaned_df[~cleaned_df['Sample_No.'].str.contains('ERR', na=False)]
    cleaned_df = cleaned_df.sort_values(by=['DateTime'], ignore_index=True)

    return cleaned_df


def _classify_samples(cleaned_df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """
    Split the cleaned dataframe into the five XN-1000 test categories,
    mirroring the original script's sequential groupby splits.

    Args:
        cleaned_df: Output of _load_and_clean().

    Returns:
        Dict mapping test category name to its DataFrame:
        CBCDC, CBC, RETIC, BACKGROUND, FUNC_ERR.

    Raises:
        ValueError: If a split doesn't produce exactly two groups (e.g. all
            rows are, or none are, functional errors) - propagates up to
            process()'s generic except-and-wrap handler.
    """
    func_error_grouped = cleaned_df.groupby(cleaned_df['Error(Func.)'] == 'Func.')
    df1, func_error = [x for _, x in func_error_grouped]

    background_grouped = df1.groupby(df1['Sample_No.'].str.strip() == 'BACKGROUNDCHECK')
    df2, background = [x for _, x in background_grouped]
    df2 = df2.copy()  # avoid SettingWithCopyWarning on the column writes below

    columns_for_counts = df2.columns[1:3]
    for column in columns_for_counts:
        df2[column] = df2[column].apply(data_convrt)

    retic_grouped = df2.groupby(df2['RET%(%)'] == 1)
    df3, retic = [x for _, x in retic_grouped]

    cbcdc_grouped = df3.groupby(df2['NEUT%(%)'] == 1)
    cbc, cbcdc = [x for _, x in cbcdc_grouped]

    return {
        'CBCDC': cbcdc,
        'CBC': cbc,
        'RETIC': retic,
        'BACKGROUND': background,
        'FUNC_ERR': func_error,
    }


# ---------------------------------------------------------------------------
# Legacy standalone CLI usage (unchanged behavior): `python xn.py`
# ---------------------------------------------------------------------------

def main():
    csvs = get_files_from_dir(input("Enter Root folder Name : "))
    print("CSVs Files : ")
    for csv in csvs:
        print(csv)

    # csvs = get_files()
    # creating list of dataframes from csv files
    dfs = (pd.read_csv(csv, low_memory=False) for csv in csvs)
    # concating data frames in list to one dataframe
    df =  pd.concat(dfs, ignore_index=True)
    # cleaning dataframe columns for white spaces
    df.columns = df.columns.str.strip().str.replace(' ', '_')
    # filtering dataframe for columns from column_needed list
    cleaned_df = df.filter(items=columns_needed)
    # New column "cleaned_df ['Time']"" created by joining(contatination) date and time column
    cleaned_df["DateTime"] = cleaned_df["Date"].astype(str) + " " + cleaned_df["Time"]
    # converting Time column data from string to datetime object
    cleaned_df["DateTime"] = pd.to_datetime(cleaned_df["DateTime"], format= '%d-%m-%Y %H:%M:%S')
    # cleaned_df["DateTime"] = pd.to_datetime(cleaned_df["DateTime"], format= '%d/%m/%Y %H:%M:%S')
    # droping the individual date and time columns as time column created  
    cleaned_df.drop(columns=['Date', 'Time'], inplace= True)
    #removing results from sample colums with ERR 
    cleaned_df = cleaned_df.drop(cleaned_df[(cleaned_df['Sample_No.'].str.contains('ERR')) ].index)
    #sorting date wise
    cleaned_df = cleaned_df.sort_values(by=['DateTime'], ignore_index=True )
    # filtering for fuctional error and creating two df, df1 without error and func_error is with error
    func_error_grouped = cleaned_df.groupby(cleaned_df['Error(Func.)'] == 'Func.')
    df1 , func_error = [x for _, x in func_error_grouped] # Create separate DataFrames
    # filtering for fuctional error and creating two df, df2 without BACKGROUNDCHECK and background is with BACKGROUNDCHECK
    background_grouped = df1.groupby(df1['Sample_No.'].str.strip() == 'BACKGROUNDCHECK')
    df2 , background = [x for _, x in background_grouped]  # Create separate DataFrames
    # applying data convert funtion on colums 1 and 2
    columns_for_counts = df2.columns[1:3]
    for column in columns_for_counts:
        df2[column] = df2[column].apply(data_convrt)
    # filtering for retic and creating two df, df3 without retic and retic is with retic
    retic_grouped = df2.groupby(df2['RET%(%)'] == 1)
    df3 , retic = [x for _, x in retic_grouped]  # Create separate DataFrames
    # filtering for netut% and creating two df, cbc without neutro and cbcdc is with neutro
    cbcdc_grouped = df3.groupby(df2['NEUT%(%)'] == 1)
    cbc , cbcdc = [x for _, x in cbcdc_grouped]  # Create separate DataFrames
    #get start time from sorted first row[0] time to use for file name and folder name creation
    start_time = cleaned_df["DateTime"][0]
    # get endtime similarly iloc[-1]['column_name']
    end_time = cleaned_df.iloc[-1]['DateTime']
    # Creating output path for file to be saved with folder and file with name of month
    output_path = f'./{start_time.strftime("%b")}{start_time.strftime("%Y")}/out_XN1000_{start_time.strftime("%b")}_{start_time.strftime("%Y")}.csv'
    # Extract the directory path from the full file path
    output_dir = os.path.dirname(output_path)
    # Create the directory recursively if it does not exist
    # The exist_ok=True argument prevents an error if the directory already exists
    os.makedirs(output_dir, exist_ok=True)
    # create new_content empty string for summary count
    new_content = ""
    # create final_dc dic for iteration and getting name of test from key
    final_dfs = {'CBCDC' :cbcdc, 
                'CBC' : cbc, 
                'RETIC' :retic, 
                'BACKGROUND' : background, 
                'FUNC_ERR' : func_error }
    # iterate for each test
    for name,df in final_dfs.items():
        # create new column with content "name"
        df['Test'] = name
        # filter only required colums in csv
        df = df.filter(items=final_columns_needed)
        # save df to csv 
        df.to_csv(output_path, mode='a', index=False)
        # amend new_content string for test and count to be added to file later
        new_content += f"{name}, {len(df)}" + "\n"
        # print count of test
        print(f"{name}, {len(df)}")
    print("Data for Period ", start_time, " to ", end_time, " Saved to ", output_path )
    # update new content on top of the file
    with open(output_path, "r+") as f:
        old_content = f.read()
        f.seek(0)  # Move cursor to the beginning
        f.write(new_content + old_content)



#Selecting required colums 
columns_needed = ['Sample_No.','Date', 'Time', 'RET%(%)', 'NEUT%(%)', 'Error(Func.)' ]
final_columns_needed = ['Sample_No.','DateTime', 'Test' ]
# defining function that will convert values to 1 in dataframe for count
def data_convrt(x):
    try:
        y = float(x)
        if y >= 0:
            return 1
        return 0
    except:
        if re.search("---", x):
            return 1
        else:
            return 0




def get_files_from_dir(dir):
    path = Path(os.path.join(os.getcwd(), dir, "xn1000" ))

    # Using glob to find specific file types
    csvs  = list(path.glob("*.csv"))
    # print(samples)
    # print(qcs)
    if not csvs:
        sys.exit(f"files not found  at {path}!!")
    else:
        return csvs

# #defining function to get file from command prompt
# def get_files():
#     while True:
#         no_of_files = int(input("Please enter Number of CSV files You Need to analyse: "))
#         csvs = []
#         for i in range(no_of_files):
#             file = input(f"Please enter file{i+1} name with suffix: ")
#             if os.path.isfile(file):
#                 csvs.append(file)
#         # checking if all files added to csv list i.e. all file path are valid
#         if len(csvs) == no_of_files:
#             return csvs
#         else:
#             print("Invalid File Names! ")     





if __name__ == "__main__":
    main()