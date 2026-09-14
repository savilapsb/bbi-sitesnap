
# Imports
import json
import logging
import pathlib
import time

# Local imports
from sitesnap_thread_functions import run_async_entrypoint
from sitesnap_snap_functions import (
    clear_directory,
    get_snaps,
)
from sitesnap_utils import (
    get_brand_info,
    print_log,
    user_confirm,
)

# Logging
logger = logging.getLogger(__name__)


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
    # Trouble List Levels:
    #    2 = A little trouble, use stealth settings
    #    3 = More trouble, use BrightData WebUnlocker
    #    None = Obnoxious, needs manual screenshots
    ###########################################################################################
    n_snaps: int = 10
    max_height: int = 30_000  # max height of screenshots, in pixels
    trouble_list: dict = {
        "Hoka": None,          ### NEEDS MANUAL SNAPS ###
        "Adidas": None,        ### NEEDS MANUAL SNAPS ###  (was 3, now BrightData not working)
        "Crocs": None,         ### NEEDS MANUAL SNAPS ###
        "New Balance": None,   ### NEEDS MANUAL SNAPS ###  (was 3, now BrightData not working)
        "Vans": None,          ### NEEDS MANUAL SNAPS ###  (was 3, now BrightData not working)
        "Volvo": 2,
        "Sephora": None,       ### NEEDS MANUAL SNAPS ###
        "Huda Beauty": None,   ### NEEDS MANUAL SNAPS ###  (was 3, now BrightData not working)
        "Estee Lauder": None,  ### NEEDS MANUAL SNAPS ###  (was 3, now BrightData not working)
        "Lululemon": None,     ### NEEDS MANUAL SNAPS ###
        "MyFitnessPal": 2,
        "New York Life Insurance": 2,
    }
    
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
        "On Running",
        
        "Toyota",
        "Kia",
        "Rivian",
        "Jeep",
        "Volvo",
        "Volkswagen",
        "Ford",
        "Mercedes",
        "Mazda",
        "BMW",
        
        "Sephora",
        "Huda Beauty",
        "Glossier",
        "Estee Lauder",
        "MAC",
        "elf",
        "L'Oreal",
        "Garnier",
        "Neutrogena",
        "Laneige",
        
        "Apple Watch",
        "MyFitnessPal",
        "Oura",
        "Garmin",
        "Strava",
        "Peloton",
        "Whoop",
        "Calm",
        "Amazfit",
        "Samsung Health",
        
        "Chase",
        "Venmo",
        "Chime",
        "PayPal",
        "Truist",
        "Schwab",
        "Ethereum",
        "Bank of America",
        "Robinhood",
        "American Express",
        "Citi",
        "Northwestern Mutual Life",
        "Zelle",
        "New York Life Insurance",
        
        "eBay",
        "Target",
    ]
    
    ###########################################################################################
    # Do the thing
    ###########################################################################################
    start_time = time.time()
    
    # Ask user to vertify date and brands
    user_confirm(year, month, brands_filter)
    
    # Run directory & logging
    script_name = "sitesnap_get_snaps"
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
    
    # Loop over brands
    print_log("\nLooping over brands...")
    for brand_info in brand_info_list:
        
        try:
            
            # Brand info
            brand_industry = brand_info['industry']
            brand_name = brand_info['name']
            brand_base_url = brand_info['website']
            brand_n_snaps = n_snaps
            trouble_level = 1
            if brand_name in trouble_list:
                trouble_level = trouble_list[brand_name]
            
            # Move forward with this brand row
            print_log(f"\t - {brand_name}")
            logger.info(f"\t\t - Trouble Level: {trouble_level}")
            
            # Skip if snaps need to be done manually
            if trouble_level == None:
                print_log("\t\t - SKIPPING SNAPS. NEEDS TO BE DONE MANUALLY.")
                continue
            
            # Create brand folders, if needed
            brand_dir = pathlib.Path(brand_info['brand_dir'], "sitesnap")
            brand_date_dir = pathlib.Path(brand_dir, "archive", time_stamp)
            brand_dir.mkdir(parents=True, exist_ok=True)
            brand_date_dir.mkdir(parents=True, exist_ok=True)
            
            # Load imporant links from json
            important_links_json_path = pathlib.Path(brand_dir, f"{brand_name}_links.json")
            if important_links_json_path.exists():
                with important_links_json_path.open("r", encoding="utf-8") as f:
                    important_links = json.load(f)
                print_log(f"\t\t - Found {len(important_links)} important link/s")
            else:
                e_message: str = f"\n***ERROR*** {brand_name} - Important Links JSON not found at: {important_links_json_path}\n"
                logger.error(e_message)
                raise FileNotFoundError(e_message)
            
            # Trim links
            important_links = [ item.strip().strip("'") for item in important_links ]
            
            # Delete all old snaps
            clear_directory(brand_dir, extensions=[".jpeg"])
            
            # Get snaps
            print_log("\t\t - Taking snaps...")
            run_async_entrypoint(
                get_snaps,
                urls=important_links,
                image_dir=brand_dir,
                archive_dir=brand_date_dir,
                trouble_level=trouble_level,
                headless=False,
                image_type="jpeg", # png or jpeg
                delay=5,
                max_height=max_height,
            )
            
        except Exception as e:
            print_log(f"\t\t***EXCEPTION ENCOUNTERED FOR '{brand_name}': {e}")
    
    # Finished
    time_elapsed = time.time() - start_time
    time_elapsed_min = int(time_elapsed // 60)
    time_elapsed_sec = round(time_elapsed % 60, 2)    
    print_log(f"Total Run Time : {time_elapsed_min} min. {time_elapsed_sec} sec.")
    print_log("\n***END***")
    logging.shutdown()
