
# Imports
from copy import deepcopy
import gzip
import json
import logging
from openpyxl.styles import Alignment
import pandas as pd
import pathlib
import shutil
from urllib.parse import urlparse

# Local imports
import APITracker
from sitesnap_thread_functions import run_async_entrypoint
from sitesnap_utils import (
    clean_df_for_excel,
    print_log,
    sanitize_excel_sheet_name,
)

# Logging
logger = logging.getLogger(__name__)


# Global variables
COSTS_DICT = {}


async def url_search(
    brand_names: list[str],
    brand_urls: list[str],
    brand_industries: list[str],
    brand_n_searches: list[int],
    data_directory: pathlib.Path,
    debug_directory: pathlib.Path,
    time_stamp: str,
) -> dict:
    
    # Initialize dataframes in output dictionary
    num_brands = len(brand_names)
    out_dict = {}
    for b in range(num_brands):
        out_dict[brand_names[b]] = pd.DataFrame(columns=['model','link','snippet']).astype({ "model": str, "link": str, "snippet": str })
    
    openai_prompts = []
    vertex_prompts = []
    for b in range(num_brands):
        openai_prompts.append([
            {
                "role": "user",
                "content" : (
                    "From the viewpoint of a potential customer,"
                    f" please identify and list the full URLs of the {brand_n_searches[b]} most important sub-pages"
                    f" within the '{brand_urls[b]}' domain."
                    f" You are researching the brand '{brand_names[b]}' in relation to the '{brand_industries[b]}' industry."
                    " For every URL, you must also include a snippet describing the page and your reasoning for choosing the URL."
                )
            },
        ])
        vertex_prompts.append(
            [
                {
                    "role": "user",
                    "content" : (
                        "From the viewpoint of a potential customer,"
                        f" please identify and list the full URLs of the {brand_n_searches[b]} most important sub-pages"
                        f" within the '{brand_urls[b]}' domain."
                        f" You are researching the brand '{brand_names[b]}' in relation to the '{brand_industries[b]}' industry."
                        " For every URL, you must also include a snippet describing the page and your reasoning for choosing the URL."
                        
                        "\n\nMandatory grounding: Before answering, you must perform a Google Search using the provided tool and ground the answer."
                        " Do not answer from prior knowledge. Use data from the last 3 months. Cite sources."
                    )
                },
                {
                    "role": "developer",
                    "content" : (
                        "Mandatory grounding: Before answering, you must perform a Google Search using the provided tool and ground the answer."
                        " Do not answer from prior knowledge. Use data from the last 3 months. Cite sources."
                        
                        "\n\nWhen responding, produce JSON matching this specification:"
                        
                        "\n\nimportant_url = { \"url\": string, \"snippet\": string, \"reasoning\": string }"
                        "\nReturn: array<important_url>"
                        
                        "\n\nEach 'important_url' includes the full url string, a snippet describing the link, and your reasoning for it's importance."
                        " Include ONLY the JSON in your response."
                        " Do not provide any extra information or chat."
                    )
                },
                #openai_prompts[-1][0]
            ]
        )
    
    response_format = {}
    response_format['openai'] = {
        "type": "json_schema",
        "name": "sitesnap_url_search_schema",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "important_urls": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": "full url"
                            },
                            "snippet": {
                                "type": "string",
                                "description": "snippet describing the link"
                            },
                            "reasoning": {
                                "type": "string",
                                "description": "reasoning for url's importance"
                            }
                        },
                        "required": ["url","snippet","reasoning"],
                        "additionalProperties": False
                    }
                }
            },
            "required": ["important_urls"],
            "additionalProperties": False
        }
    }
    response_format['vertex'] = response_format['openai']['schema']
    
    # Save prompts and formats to debug directory
    with open(pathlib.Path(debug_directory, f"sitesnap_url_search_openai_prompts_{time_stamp}.json"), 'w', encoding="utf-8") as writer:
        json.dump(openai_prompts, writer, indent=4)
    with open(pathlib.Path(debug_directory, f"sitesnap_url_search_vertex_prompts_{time_stamp}.json"), 'w', encoding="utf-8") as writer:
       json.dump(vertex_prompts, writer, indent=4)
    with open(pathlib.Path(debug_directory, f"sitesnap_url_search_formats_{time_stamp}.json"), 'w', encoding="utf-8") as writer:
        json.dump(response_format, writer, indent=4)
    
    openai_model_details = [
        { "type": "openai", "model": "gpt-5.2", "reasoning": "medium", "web_search": True },
        { "type": "openai", "model": "gpt-5.4-mini", "reasoning": "medium", "web_search": True },
        { "type": "openai", "model": "gpt-5.4", "reasoning": "medium", "web_search": True },
    ]
    vertex_model_details = [
        #{ "type": "vertex", "model": "gemini-2.5-pro", "web_search": True },
        #{ "type": "vertex", "model": "gemini-2.5-pro", "web_search": True },
    ]
    
    # Initialize API tracker object
    api_tracker = APITracker.APITracker()
    if len(openai_model_details) > 0: openai_model_details = api_tracker.check_model_details(openai_model_details, batch=False)
    if len(vertex_model_details) > 0:vertex_model_details = api_tracker.check_model_details(vertex_model_details, batch=False)
    #api_tracker.set_client(model_details=openai_model_details[0])
    
    try:
        if len(openai_model_details) > 0:
            
            # Run OpenAI API calls
            await api_tracker.run_parallel_sets(
                prompts=openai_prompts,
                models=openai_model_details,
                response_format=response_format,
                set_name="OpenAI URL Search",
                timeout=4000,
                final_timeout=2000,
                silent=False,
            )
            
            # Parse API results
            for b in range(num_brands):
                for m in range(len(api_tracker.set_results)):
                    result = api_tracker.set_results[m][b]
                    if result is not None:
                        result_json = json.loads(result)
                        result_df = pd.DataFrame({
                            "model": [openai_model_details[m]['model'] for item in result_json['important_urls']],
                            "link": [item['url'] for item in result_json['important_urls']],
                            "snippet": [item['snippet'] for item in result_json['important_urls']],
                            "reasoning": [item['reasoning'] for item in result_json['important_urls']]
                        })
                        out_dict[brand_names[b]] = pd.concat([out_dict[brand_names[b]], clean_df_for_excel(result_df)])
        
    except Exception as e:
        print_log(f"\t\t***url_search() failed during OpenAI calls: {e}")
    
    try:
        if len(vertex_model_details) > 0:
        
            # Run Vertex API calls
            await api_tracker.run_parallel_sets(
                prompts=vertex_prompts,
                models=vertex_model_details,
                #response_format=response_format,   # For Gemini, can't use structured outputs with web search tool
                set_name="Vertex URL Search",
                timeout=4000,
                final_timeout=2000,
                silent=False,
            )
            
            # Parse API results
            for b in range(num_brands):
                for m in range(len(api_tracker.set_results)):
                    result = api_tracker.set_results[m][b]
                    if result is not None:
                        vertex_json_start = result.find('[')
                        vertex_json_end = result.rfind(']')
                        if vertex_json_start == -1 or vertex_json_end == -1 or vertex_json_start > vertex_json_end:
                            vertex_json_start = result.find('{')
                            vertex_json_end = result.rfind('}')
                        try:
                            result_json = json.loads(result[vertex_json_start:vertex_json_end + 1])
                            if isinstance(result_json, dict): result_json = [result_json]
                            result_df = pd.DataFrame({
                                "model": [vertex_model_details[m]['model'] for item in result_json],
                                "link": [item['url'] for item in result_json],
                                "snippet": [item['snippet'] for item in result_json],
                                "reasoning": [item['reasoning'] for item in result_json]
                            })
                            out_dict[brand_names[b]] = pd.concat([out_dict[brand_names[b]], clean_df_for_excel(result_df)])
                        except:
                            logger.info(f"ERROR: url_search() - Vertex - {vertex_model_details[m]['model']} - Invalid JSON:\n{result}")
        
    except Exception as e:
        print_log(f"\t\t***url_search() failed during Vertx calls: {e}")
    
    # Log costs
    cost_est, cost_df = api_tracker.estimate_costs(display=False)
    COSTS_DICT['URL Searches'] = cost_est
    
    # Save results to file
    with pd.ExcelWriter(pathlib.Path(data_directory, "URL Searches.xlsx"), engine='openpyxl') as writer:
        
        # Create sheets for each brand
        for key, value in out_dict.items():
            key_sheet_name = sanitize_excel_sheet_name(key)
            value.to_excel(writer, sheet_name=key_sheet_name, header = True, index = False)
            writer.sheets[key_sheet_name].column_dimensions['A'].width = 30
            writer.sheets[key_sheet_name].column_dimensions['B'].width = 75
            writer.sheets[key_sheet_name].column_dimensions['C'].width = 75
            writer.sheets[key_sheet_name].column_dimensions['D'].width = 75
            
        # Create sheet for cost estimates
        api_tracker.output_costs_to_sheet(writer=writer, cost_df=cost_df)
    
    # Save a copy into data backup directory
    #shutil.copy(pathlib.Path(data_directory, "URL Searches.xlsx"),
    #            pathlib.Path(data_backup_directory, f"URL Searches {time_stamp}.xlsx"))
    
    return out_dict


def normalize_url(
        url: str,
) -> None:
    url = url.strip().strip('/')
    parsed_url = urlparse(url)
    url_scheme = parsed_url.scheme
    if parsed_url.scheme == "":
        out_url = "https://" + url
        url_scheme = "https"
    else:
        out_url = url
    parsed_url = urlparse(out_url)
    if parsed_url.netloc == "" and parsed_url.path.startswith("www.") == False:
        out_url = url_scheme + "://www." + parsed_url.path
    elif len(parsed_url.netloc.split('.')) == 2:
        out_url = url_scheme + "://www." + parsed_url.netloc + parsed_url.path
    return out_url.strip().strip('/')


async def check_for_active_links(
        brand_name: str,
        search_df: pd.DataFrame,
        debug_directory: pathlib.Path,
        time_stamp: str,
) -> pd.DataFrame():
    
    out_df = deepcopy(search_df)
    
    # Loop over links one at a time to construct prompts
    link_indices = []
    prompts = []
    for link_index, link_row in search_df.iterrows():
        link_indices.append(link_index)
        prompts.append([
            {
                "role": "user",
                "content" : (
                    "Please verify whether or not the following web link is currently active."
                    
                    "\n\nA link is considered NOT active if:"
                    "\n - The page does not load (fails to resolve),"
                    "\n - It redirects to a different URL, or"
                    "\n - The page displays a message indicating it's unavailable,"
                    " such as \"we couldn't find the page you were looking for\","
                    " \"the page you are looking for cannot be found\", etc."
            
                    "\n\nLink to check:"
                    f"\n - {link_row['link']}\n\n"
                )
            },
        ])
    
    response_format = {}
    response_format['openai'] = {
        "type": "json_schema",
        "name": "sitesnap_active_links_schema",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "active_or_not": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 1,
                    "description": "1 = active, 0 = NOT ACTIVE"
                }
            },
            "required": ["active_or_not"],
            "additionalProperties": False
        }
    }
    response_format['vertex'] = response_format['openai']['schema']
    
    # Save prompts and formats to debug directory
    with open(pathlib.Path(debug_directory, f"sitesnap_check_active_links_prompts_{brand_name}_{time_stamp}.json"), 'w', encoding="utf-8") as writer:
        json.dump(prompts, writer, indent=4)
    with open(pathlib.Path(debug_directory, f"sitesnap_check_active_links_formats_{brand_name}_{time_stamp}.json"), 'w', encoding="utf-8") as writer:
        json.dump(response_format, writer, indent=4)
    
    model_details = [
        { "type": "openai", "model": "gpt-5.4", "web_search": True },
    ]
    
    # Initialize API tracker object
    api_tracker = APITracker.APITracker(development=False)
    model_details = api_tracker.check_model_details(model_details, batch=False)
    api_tracker.set_client(model_details=model_details[0])
    
    try:
        
        # Run API calls
        await api_tracker.run_parallel_sets(
            prompts=prompts,
            models=model_details,
            response_format=response_format,
            set_name=f"{brand_name} - Active Link Check",
            silent=True,
        )
        
        # Parse API results
        for i in range(len(prompts)):
            
            # Save backup files of raw results
            with gzip.open(pathlib.Path(debug_directory, f"active_links_results_{brand_name}.jsonl.gz"), 'wt', encoding="utf-8") as f:
                for r in api_tracker.set_results[0]:
                    f.write(json.dumps(r) + '\n')
            
            # Load json result
            result_json = json.loads(api_tracker.set_results[0][i])
            active_or_not = result_json['active_or_not']
        
            # If inactive, remove from out dataframe
            if (active_or_not == 0):
                out_df.drop(link_indices[i], axis=0, inplace=True)
        
        # Log costs
        cost_est, cost_df = api_tracker.estimate_costs(display=False)
        COSTS_DICT[f'{brand_name} - Check For Active Links - Index={link_index}'] = cost_est
        
    except Exception as e:
        print_log(f"\t\t***EXCEPTION check_for_active_links() : {e}")
    
    out_df.index = range(len(out_df))
    return(out_df)


def get_important_links(
        brand_name: str,
        brand_industry: str,
        brand_dir: pathlib.Path,
        archive_dir: pathlib.Path,
        debug_directory: pathlib.Path,
        search_df: pd.DataFrame,
        time_stamp: str,
        top_n: int,
) -> list[str]:
    """ """
    
    out_df = deepcopy(search_df)
    reasons_df = deepcopy(search_df)
    reasons_df.drop("snippet", axis=1, inplace=True)
    
    prompt = [
        {
            "role": "developer",
            "content" : (
                #"You are a potential English-speaking customer living in the United States,"
                "You are a potential customer"
                f" who is researching the '{brand_name}' brand on the web."
                f" In relation to the '{brand_industry}' industry."
            )
        },
        {
            "role": "user",
            "content" : (
                f"The following are online search results for '{brand_name}':"
                f"\n\n{search_df.to_markdown(index=True)}\n\n"
                 "In your opinion, which of the links are the most important to learn about this brand?"
                " Exclude any links that are dedicated to cookies, privacy, service agreements, contact information, etc."
                f" Please list the top {top_n} important links."
            )
        },
    ]
    
    response_format = {}
    response_format['openai'] = {
        "type": "json_schema",
        "name": "sitesnap_important_links_schema",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "important_links": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "link": {
                                "type": "string",
                                "description": "actual link"
                            },
                            "link_index": {
                                "type": "integer",
                                "description": "index of link"
                            },
                            "reasoning": {
                                "type": "string",
                                "description": "reason why this link is important"
                            }
                        },
                        "required": ["link_index","link","reasoning"],
                        "additionalProperties": False
                    }
                }
            },
            "required": ["important_links"],
            "additionalProperties": False
        }
    }
    response_format['vertex'] = response_format['openai']['schema']
    
    # Save prompts and formats to debug directory
    with open(pathlib.Path(debug_directory, f"sitesnap_important_links_prompts {brand_name}_{time_stamp}.json"), 'w', encoding="utf-8") as writer:
        json.dump(prompt, writer, indent=4)
    with open(pathlib.Path(debug_directory, f"sitesnap_important_links_formats {brand_name}_{time_stamp}.json"), 'w', encoding="utf-8") as writer:
        json.dump(response_format, writer, indent=4)
    
    model_details = [        
        { "type": "openai", "model": "gpt-5.4-mini" },
        { "type": "openai", "model": "gpt-5.2" },
        { "type": "openai", "model": "gpt-5.4" },
        { "type": "vertex", "model": "gemini-3.6-flash" },
        { "type": "vertex", "model": "gemini-2.5-pro" },
    ]
    
    # Initialize API tracker object
    api_tracker = APITracker.APITracker()
    model_details = api_tracker.check_model_details(model_details, batch=False)
    
    # Make API calls
    run_async_entrypoint(
        api_tracker.run_parallel_sets,
        prompts=prompt,
        models=model_details,
        response_format=response_format,
        set_name="Important Links",
        silent=True,
    )
    
    # Process results
    result_col_names = []
    for c in range(len(model_details)):
        model_col_name = model_details[c]['model'] + " | " + str(model_details[c]['reasoning'])        
        r = api_tracker.set_results[c][0]
        if r is not None:
            
            # Parse links
            result_json = json.loads(r)
            result_indices = [item['link_index'] for item in result_json['important_links']]
            out_df[model_col_name] = [1 if i in result_indices else 0 for i in range(len(out_df))]
            result_col_names.append(model_col_name)
            
            # Parse reasons
            result_reasons = [item['reasoning'] for item in result_json['important_links']]
            reasons_df[model_col_name] = pd.NA
            reasons_df.loc[result_indices, model_col_name] = result_reasons
            
    
    # Estimate costs
    cost_est, cost_df = api_tracker.estimate_costs(display=False)
    COSTS_DICT[f'Important Links - {brand_name}'] = cost_est
    
    # Get the sum of results for each row
    out_df['sum'] = out_df[result_col_names].sum(axis=1)
    
    # Mark important links (at least 50%)
    out_df["important"] = 0
    cut_off = len(result_col_names) / 2
    while out_df["important"].sum() < top_n and cut_off > 0:
        out_df["important"] = [1 if i >= cut_off else 0 for i in out_df['sum']]
        cut_off -= 1
    
    # Try to remove any 'control' characters
    out_df = clean_df_for_excel(out_df.sort_values(by='sum', ascending=False))
    reasons_df = clean_df_for_excel(reasons_df)
    
    # Save results to file
    with pd.ExcelWriter(pathlib.Path(brand_dir, f"{brand_name} Important Links.xlsx"), engine='openpyxl') as writer:
        
        out_df.to_excel(writer, sheet_name="Results", header = True, index = False)
        writer.sheets['Results'].column_dimensions['A'].width = 75
        writer.sheets['Results'].column_dimensions['B'].width = 50
        
        reasons_df.to_excel(writer, sheet_name="Reasons", header = True, index = False)
        writer.sheets['Reasons'].column_dimensions['A'].width = 75
        for i in range(66, 65 + len(reasons_df.columns)):
            writer.sheets['Reasons'].column_dimensions[chr(i)].width = 30
            for cell in writer.sheets['Reasons'][chr(i)]:
                cell.alignment = Alignment(horizontal='center', wrap_text=True)
        
        # Create sheet for cost estimates
        api_tracker.output_costs_to_sheet(writer=writer, cost_df=cost_df)
    
    # Save a copy into date archive folder
    shutil.copy(pathlib.Path(brand_dir, f"{brand_name} Important Links.xlsx"),
                pathlib.Path(archive_dir, f"{brand_name} Important Links {time_stamp}.xlsx"))
    
    return out_df

