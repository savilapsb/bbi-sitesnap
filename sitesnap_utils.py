from copy import deepcopy
import logging
import pandas as pd
import pathlib
import re
import sys

# logging
logger = logging.getLogger(__name__)


def print_log(
        message: str,
) -> None:
    """
    Utility function that prints message to console and to log file.
    
    Args:
        message (str): The message to print.
    """
    
    print(message)
    logger.info(message)


def user_confirm(
        year: int,
        month: int,
        brands_filter: list[str],
) -> None:

    brands_to_run_string = "ALL"
    if len(brands_filter):
        brands_to_run_string = str(len(brands_filter))
    user_confirmation = input(
        "\n*****"
        f"\nYear: {year}"
        f"\nMonth: {month}"
        f"\n# Brands: {brands_to_run_string}"
        f"\n*****"
        "\n — Is this correct? (y/n): "
    ).strip().lower()
    if user_confirmation != "y":
        print("Please update the values and re-run the script.")
        sys.exit()


def shorten_psb_path(
        p: str,
        marker: str = "/PSB/",
) -> str:
    try:
        s = pathlib.Path(p).as_posix()
    except:
        raise TypeError(f"path incorrect type: '{type(p)}'")
    i = s.find(marker)
    if i == -1:
        raise ValueError(f"'{marker}' not found in: {s}")
    tail_from_psb = s[i:]
    return(tail_from_psb)


def clean_string_for_excel(v):
    # Convert any container classes into strings, so that they get shortened as well
    if isinstance(v, (dict, list, tuple, set)):
        v = str(v)
    if not isinstance(v, str):
        return v
    v = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]").sub("", v)
    if v.startswith(("=", "http://", "https://")):
        v = "'" + v
    if len(v) > 32700:
        v = v[:32700]
    return v


def clean_df_for_excel(x):
    df_out = deepcopy(x)
    for col in df_out.columns:
        #if df_out[col].dtype == 'object':
        if pd.api.types.is_string_dtype(df_out[col]) or pd.api.types.is_object_dtype(df_out[col]):
            df_out[col] = df_out[col].map(clean_string_for_excel)
    return df_out


def sanitize_excel_sheet_name(name, fallback="Sheet1"):
    """
    Convert an arbitrary string into a legal Excel sheet/tab name.

    Excel rules enforced:
      - Max 31 characters
      - Cannot contain: \\ / ? * [ ] :
      - Cannot be blank
      - Cannot begin or end with an apostrophe (')
      - Cannot be the reserved name "History"
    """
    # Replace illegal characters with an underscore
    cleaned = re.sub(r'[\\/?*\[\]:]', '_', str(name))

    # Trim leading/trailing apostrophes and whitespace
    cleaned = cleaned.strip().strip("'").strip()

    # Enforce the 31-character limit
    cleaned = cleaned[:31]

    # Trim again in case truncation left a trailing apostrophe/space
    cleaned = cleaned.strip().strip("'").strip()

    # Handle empty result or reserved name
    if not cleaned or cleaned.lower() == "history":
        cleaned = fallback

    return cleaned


def load_lexisnexis_query(
        query_paths: list[pathlib.Path],
        sep: str = " AND ",
) -> str:
    
    # Initialize empty query
    out_query = ""
    
    # Loop over files, adding each to the query
    for query_path in query_paths:
        
        # Open file and extract text
        query_file_path = pathlib.Path(query_path)
        try:
            with open(query_file_path, 'r', encoding='utf-8') as f:
                current_query = f.read()
        except FileNotFoundError:
            print(f"load_query() - Error: The file '{query_file_path}' was not found.")
        except Exception as e:
            print(f"load_query() - An error occurred: {e}")
            
        # Format text to remove unnecessary white space
        current_query = re.sub(r"\s+", " ", current_query).strip()
        
        # Add to master query
        if out_query == "":
            out_query = current_query
        else:
            out_query = out_query + sep + current_query
    
    # Return
    return out_query


def get_brand_info(
        brand_info_path: pathlib.Path,
        brand_info_sheet: str,
        brands_filter: list[str],
        bbi_path: pathlib.Path,
        year: int,
        month: int,
        topic_id_dict: dict[str,dict] = None,
) -> list[dict]:
    
    # Open BBI Brand Info Sheet
    logger.info("Opening brand info file...")
    if brand_info_path.exists():
        brand_info_df = pd.read_excel(brand_info_path, sheet_name=brand_info_sheet)
    else:
        e_message = f"Brand info file not found!: {str(brand_info_path)}"
        print_log(e_message)
        raise FileNotFoundError(e_message)
    if len(brand_info_df.index) == 0:
        e_message = f"Brand info sheet '{brand_info_sheet}' contains zero brands!: {str(brand_info_path)}"
        print_log(e_message)
        raise FileNotFoundError(e_message)
    
    # Check brand info
    logger.info("Checking brand list...")
    brand_run_list = []
    for brand_index, brand_row in brand_info_df.iterrows():
            
        # Brand info
        if pd.isna(brand_row['brand']):
            brand_name = ""
        else:
            brand_name = str(brand_row['brand']).strip()
        if pd.isna(brand_row['industry']):
            brand_industry = ""
        else:
            brand_industry = str(brand_row['industry']).strip()
        if pd.isna(brand_row['website']):
            brand_website = ""
        else:
            brand_website = str(brand_row['website']).strip()
        if pd.isna(brand_row['data_folder']):
            brand_data_folder = ""
        else:
            brand_data_folder = str(brand_row['data_folder']).strip()
        if pd.isna(brand_row['talkwalker_id']):
            brand_talkwalker_id = ""
        else:
            brand_talkwalker_id = str(brand_row['talkwalker_id']).strip()
        if pd.isna(brand_row['lexisnexis_query']):
            lexisnexis_query_path = ""
        else:
            lexisnexis_query_path = str(brand_row['lexisnexis_query']).strip()
        
        # Is this row valid?
        if brand_name == "" or brand_website == "" or brand_data_folder == "" or brand_talkwalker_id == "" or lexisnexis_query_path == "":
            e_message = (
                "A brand is missing needed info!:"
                f"\n\tbrand='{brand_name}'"
                f"\n\twebsite='{brand_website}'"
                f"\n\tdata_folder='{brand_data_folder}'"
                f"\n\ttalkwalker_id='{brand_talkwalker_id}'"
                f"\n\tlexisnexis_query='{lexisnexis_query_path}'"
            )
            print_log(e_message)
            raise ValueError(e_message)
        
        # If filtering for only certain brands
        if len(brands_filter) > 0 and brand_name not in brands_filter:
            continue
        
        if topic_id_dict is not None:
            
            # Is Talkwalker ID valid?
            if brand_talkwalker_id not in topic_id_dict:
                e_message = (
                    f"The talkwalker topic id ({brand_talkwalker_id}) for '{brand_name}' could not be found in this project!"
                )
                print_log(e_message)
                raise ValueError(e_message)
            
            # Extra topic info
            extra_info = topic_id_dict[brand_talkwalker_id]
        
        else:
            extra_info = { "title": "", "description": "" }
        
        # Load LexisNexis query
        lexisnexis_query_path = pathlib.Path(bbi_path, lexisnexis_query_path)
        if lexisnexis_query_path.exists():
            lexisnexis_query = load_lexisnexis_query(query_paths=[lexisnexis_query_path])
        else:
            e_message = (
                "Cannot find LexisNexis query!:"
                f"\n\tbrand='{brand_name}'"
                f"\n\tlexisnexis_query_path='{lexisnexis_query_path}'"
            )
            print_log(e_message)
            raise ValueError(e_message)
        
        # Add to brand info list
        brand_run_list.append({
            "name": brand_name,
            "industry": brand_industry,
            "website": brand_website,
            "brand_dir": pathlib.Path(bbi_path, brand_data_folder, str(year), str(month)),
            "id": brand_talkwalker_id,
            "title": extra_info['title'],
            "description": extra_info['description'],
            "lexisnexis_query": lexisnexis_query,
        })
        
    return brand_run_list

