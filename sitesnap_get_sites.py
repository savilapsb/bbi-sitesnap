
# Imports
from copy import deepcopy
import json
import logging
import pandas as pd
import pathlib
import re
import shutil
import time

# Local imports
from sitesnap_thread_functions import run_async_entrypoint
from sitesnap_site_functions import (
    check_for_active_links,
    get_important_links,
    normalize_url,
    url_search,
)
from sitesnap_utils import (
    get_brand_info,
    print_log,
    user_confirm,
)

# Logging
logger = logging.getLogger(__name__)


# Global variables
#COSTS_DICT = {}


if __name__ == "__main__":
    
    ###########################################################################################
    # Locate main BBI path
    ###########################################################################################
    user_path = pathlib.Path.home()
    psb_sp_name = "PSB"
    
    # OLD SharePoint paths
    # psb_team_name = "Team AI - Documents"
    # psb_sp_path = pathlib.Path(user_path, psb_sp_name)
    # if psb_sp_path.exists() == False or pathlib.Path(psb_sp_path, psb_team_name).exists() == False:
    #     psb_sp_name = "PSB(1)"
    #     psb_sp_path = pathlib.Path(user_path, psb_sp_name)
    #     if psb_sp_path.exists() == False or pathlib.Path(psb_sp_path, psb_team_name).exists() == False:
    #         raise FileNotFoundError((
    #             "Unable to locate your PSB SharePoint path!:"
    #             f"\n\t{str(pathlib.Path(user_path, 'PSB', psb_team_name).resolve())}"
    #         ))
    # bbi_path = pathlib.Path(psb_sp_path, psb_team_name, "BBI")
    
    # New network drive
    bbi_path = pathlib.Path("Y:\\")
    
    ###########################################################################################
    # Directories
    ###########################################################################################
    directory = pathlib.Path(__file__).resolve().parent
    runs_path = pathlib.Path(directory, "runs")
    
    ###########################################################################################
    # Date Range
    ###########################################################################################
    year: int = 2026
    month: int = 8
    
    ###########################################################################################
    # Options
    ###########################################################################################
    n_search: int = 30
    n_snaps: int = 10
    
    ###########################################################################################
    # Brand info files
    ###########################################################################################
    brand_info_path = pathlib.Path(bbi_path, "BBI Brand Info.xlsx")
    brand_info_sheet: str = "Brands"
    
    ###########################################################################################
    # Only pull specific brands??
    ###########################################################################################
    brands_filter = [
        
        # "Nike",
        # "Hoka",
        # "Gymshark",
        # "Adidas",
        # "Crocs",
        # "Lululemon",
        # "New Balance",
        # "Under Armour",
        # "Vans",
        # "On Running",
        
        # "Toyota",
        # "Kia",
        # "Rivian",
        # "Jeep",
        # "Volvo",
        # "Volkswagen",
        # "Ford",
        # "Mercedes",
        # "Mazda",
        # "BMW",
        
        # "Sephora",
        # "Huda Beauty",
        # "Glossier",
        # "Estee Lauder",
        # "MAC",
        # "elf",
        # "L'Oreal",
        # "Garnier",
        # "Neutrogena",
        # "Laneige",
        
        # "Apple Watch",
        # "MyFitnessPal",
        # "Oura",
        # "Garmin",
        # "Strava",
        # "Peloton",
        # "Whoop",
        # "Calm",
        # "Amazfit",
        # "Samsung Health",
        
        # "Chase",
        # "Venmo",
        # "Chime",
        # "PayPal",
        # "Truist",
        # "Schwab",
        # "Ethereum",
        # "Bank of America",
        # "Robinhood",
        # "American Express",
        # "Citi",
        # "Northwestern Mutual Life",
        # "Zelle",
        # "New York Life Insurance",
        
        # "eBay",
        # "Target",
    ]
    
    ###########################################################################################
    # Do the thing
    ###########################################################################################
    start_time = time.time()
    
    # Ask user to vertify date and brands
    user_confirm(year, month, brands_filter)
    
    # Run directory & logging
    script_name = "sitesnap_get_sites"
    date_stamp = time.strftime("%m_%d_%Y")
    time_stamp = time.strftime("%m_%d_%Y__%H_%M_%S")
    runs_directory = pathlib.Path(runs_path, date_stamp, f"{script_name}_{time_stamp}")
    runs_directory.mkdir(parents=True, exist_ok=True)
    log_file = f"{script_name}_{time_stamp}.log"
    
    # Runs sub folders
    debug_directory = pathlib.Path(runs_directory, "debug")
    debug_directory.mkdir(parents=True, exist_ok=True)
    
    # Start logging
    logging.basicConfig(filename=pathlib.Path(runs_directory, log_file),
                        filemode='w',
                        level=logging.INFO,
                        format='%(asctime)s:%(levelname)s:%(message)s',
                        force=True)
    logger.info("\n***START***")
    
    # Gather and confirm brand info
    print_log("Loading brand info from file...")
    brand_info_list: list[dict] = get_brand_info(
        brand_info_path=brand_info_path,
        brand_info_sheet=brand_info_sheet,
        brands_filter=brands_filter,
        bbi_path=bbi_path,
        year=year,
        month=month,
    )
    
    # If zero brands, exit now
    if len(brand_info_list) == 0:
        raise ValueError("Zero brands to gather!")
    
    
    # Perform OpenAI URL search for all brands that will be refreshed
    print_log("Running url searches for all brands...")
    urls_dict = run_async_entrypoint(
        url_search,
        brand_names=[ item['name'] for item in brand_info_list ],
        brand_urls=[ item['website'] for item in brand_info_list ],
        brand_industries=[ item['industry'] for item in brand_info_list ],
        brand_n_searches=[n_search] * len(brand_info_list),
        data_directory=runs_directory,
        debug_directory=debug_directory,
        time_stamp=time_stamp,
    )
    
    # Loop over brands
    print_log("\nLooping over brands...")
    for brand_info in brand_info_list:
        
        try:
            
            # Brand info
            brand_industry = brand_info['industry']
            brand_name = brand_info['name']
            brand_base_url = brand_info['website']
            brand_n_search = n_search
            brand_n_snaps = n_snaps
            
            # Move forward with this brand row
            print_log(f"\t - {brand_name}")
            
            # Create brand folders, if needed
            brand_dir = pathlib.Path(brand_info['brand_dir'], "sitesnap")
            brand_archive_dir = pathlib.Path(brand_dir, "archive")
            brand_dir.mkdir(parents=True, exist_ok=True)
            brand_archive_dir.mkdir(parents=True, exist_ok=True)
            
            # Remove duplicate URLs from earlier searches
            url_search_df = urls_dict[brand_name]
            url_search_df = url_search_df.sort_values(by='snippet', ascending=False)
            url_search_df.drop(['model','reasoning'], axis=1, inplace=True, errors='ignore')
            url_search_df['link'] = url_search_df['link'].str.split('?', n=1).str[0]  # Clean up link urls by removing all '?' options
            search_link_normalized = url_search_df['link'].str.lower().apply(lambda x: re.sub(r"\s+", '', x).strip('/'))
            duplicate_link_rows = search_link_normalized.duplicated(keep='first')
            unique_links_df = deepcopy(url_search_df)
            if duplicate_link_rows.any():
                unique_links_df = unique_links_df[~duplicate_link_rows]
            unique_links_df.index = pd.RangeIndex(0, len(unique_links_df.index))
            
            # Ask LLMs to remove links that are no longer active
            print_log("\t\t - Filtering out inactive links...")
            active_links_df = run_async_entrypoint(
                check_for_active_links,
                brand_name=brand_name,
                search_df=unique_links_df,
                debug_directory=debug_directory,
                time_stamp=time_stamp,
            )
            
            # Ask LLMs for the most important links
            print_log("\t\t - Determining most important links...")
            important_df = get_important_links(
                brand_name=brand_name,
                brand_industry=brand_industry,
                brand_dir=brand_dir,
                archive_dir=brand_archive_dir,
                debug_directory=debug_directory,
                search_df=active_links_df,
                time_stamp=time_stamp,
                top_n=brand_n_snaps,
            )
            important_links = important_df.loc[important_df['important'] == 1, 'link'].tolist()
            
            # Add the original base url to list of links, if needed
            brand_normalized_url = normalize_url(brand_base_url)
            base_present = False
            for link in important_links:
                if brand_normalized_url == normalize_url(link):
                    base_present = True
                    break
            if base_present == False:
                important_links = [brand_normalized_url] + important_links
            
            # Output important links to json file
            important_links_trimed = [ item.strip().strip("'").strip("`") for item in important_links ]
            important_links_json_path = pathlib.Path(brand_dir, f"{brand_name}_links.json")
            with important_links_json_path.open("w", encoding="utf-8") as f:
                json.dump(important_links_trimed, f, indent=4)
            shutil.copy(pathlib.Path(brand_dir, f"{brand_name}_links.json"),
                        pathlib.Path(brand_archive_dir, f"{brand_name}_links {time_stamp}.json"))
            
        except Exception as e:
            print_log(f"\t\t***EXCEPTION ENCOUNTERED FOR '{brand_name}': {e}")
    
    # Cost
    #total_cost = sum(COSTS_DICT.values())
    #print_log(f"Total Cost: ${total_cost:,.6f}")
    
    # Finished
    time_elapsed = time.time() - start_time
    time_elapsed_min = int(time_elapsed // 60)
    time_elapsed_sec = round(time_elapsed % 60, 2)    
    print_log(f"Total Run Time : {time_elapsed_min} min. {time_elapsed_sec} sec.")
    print_log("\n***END***")
    logging.shutdown()
