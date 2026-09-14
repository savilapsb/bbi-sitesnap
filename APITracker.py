# -*- coding: utf-8 -*-
"""
@author: mmorrell
Version:  2.3.0
Version Creation: 07/06/2026
"""


# imports
import asyncio
import colorama
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import (
    dataclass,
    field,
)
from google import genai
from google.auth import default as Google_Auth_Default
from google.auth.transport.requests import Request as Google_Auth_Request
from importlib.metadata import version, PackageNotFoundError
import json
import logging
import numpy as np
from openai import (
    AsyncAzureOpenAI,
    AsyncOpenAI,
    AzureOpenAI,
    OpenAI
)
import os
from packaging.version import Version
import pandas as pd
import pathlib
from pyreadstat import read_sav
import regex
import sys
import tiktoken
import time

# local imports
import APIBatch
from APIRequest import (
    APIRequest,
    APIStatus,
)

# logging
logger = logging.getLogger(__name__)

# Batch API input file limits
OPENAI_BATCH_MAX_FILE_BYTES = 209_715_200    # 200 MiB, per the OpenAI batch API error message
OPENAI_BATCH_MAX_REQUESTS = 50_000           # OpenAI batch API maximum requests per batch file
VERTEX_BATCH_MAX_FILE_BYTES = 2_147_483_648  # 2 GiB, per the Gemini batch API documentation
BATCH_TARGET_FRACTION = 0.90                 # Stay under this fraction of the max, as a safety margin


@dataclass
class APITracker:
    """Class for storing OpenAI-based API client object and tracking it's progress"""

    status: APIStatus = field(default_factory=APIStatus)
    status_list: list = field(default_factory=list)
    model: str = field(default_factory=str)
    thinking: bool = field(default_factory=bool)
    team: str = field(default_factory=str)
    region: str = field(default_factory=str)
    base: str = field(default_factory=str)
    base_type: str = field(default_factory=str)
    api_key: str = field(default_factory=str)
    api_version: str = field(default_factory=str)
    tpm: int = field(default_factory=int)
    rpm: int = field(default_factory=float)
    context_window: int = field(default_factory=float)
    max_output_tokens: int = field(default_factory=float)
    model_minimums: dict = field(default_factory=dict)
    development: bool = False
    max_parallel: int = 1000
    max_attempts: int = 3
    seconds_to_pause: int = 30
    results: list = field(default_factory=list)
    set_results: list = field(default_factory=list)
    
    
    def __post_init__(self):
        self.check_package_version("openai", "2.32.0")
        self.check_package_version("google-genai", "1.73.1")
    
    
    def check_package_version(
            self,
            name: str,
            min_version: str
    ) -> None:
        min_v = Version(min_version)
        try:
            installed_v = Version(version(name))
        except PackageNotFoundError:
            e_message = f"The '{name}' package is not installed."
            logger.error(e_message)
            raise RuntimeError(e_message)

        if installed_v < min_v:
            e_message = (
                f"Unsupported version of the '{name}' package: {installed_v}. "
                f"Please upgrade to {min_v} or higher."
            )
            logger.error(e_message)
            raise RuntimeError(e_message)
    
    
    def set_client(
            self,
            model_details: dict = None,
            type: str = None,
            team: str = None,
            region: str = None,
            model: str = None,
    ):
        """ """
        
        if model_details is not None:
            type = model_details['type']
            region = model_details['region']
            model = model_details['model']
        
        match type:
            case "openai":
                self.set_openai_client(model=model)
            case "vertex":
                self.set_vertex_client(region=region, model=model)
            case "meta_vertex":
                self.set_meta_vertex_client(region=region, model=model)
            case "azure":
                self.set_azure_client(team=team, region=region, model=model)

    
    def set_openai_client(
            self,
            model: str = "gpt-4o",
    ):
        """ """
        
        # API Info
        self.base_type = "openai"
        self.base = "https://api.openai.com/"
        self.model = model
        self.region = None
        
        # Team Test Project (for development)
        if (self.development == True):
            self.api_key = os.environ['OPENAI_DEV_KEY']
        
        # AI Team Client Work Project (for production)
        else:
            self.api_key = os.environ['OPENAI_CLIENT_KEY']
        
        # Set TPM/RPM, context window, and max output tokens
        self.set_openai_model_limits()

    
    def set_vertex_client(
            self,
            region: str = "global",
            model: str = "gemini-2.5-flash"
    ):
        """ """
        
        # API Info
        self.base_type = "vertex"        
        self.base = None
        self.model = model
        self.api_key = None
        self.region = region
        
        # Set TPM/RPM, context window, and max output tokens
        self.set_vertex_model_limits()
    
    
    def set_meta_vertex_client(
            self,
            region: str = "us-east5",
            model: str = "meta/llama-4-maverick-17b-128e-instruct-maas"
    ):
        """ """
        
        credentials, _ = Google_Auth_Default()
        auth_request = Google_Auth_Request()
        credentials.refresh(auth_request)
        
        # API Info
        self.base_type = "meta_vertex"        
        self.base = None
        self.model = model
        self.api_key = credentials.token
        self.region = region
        
        # Set TPM/RPM, context window, and max output tokens
        self.set_vertex_model_limits()
    
    
    def set_azure_client(
            self,
            team: str = None,
            region: str = None,
            model: str = None
    ):
        """ """
    
        # Azure OpenAI Info
        self.base_type = "azure"
        
        team_list = [
            "team1",
            "team2",
            "team3",
            "team4",
            "team5",
        ]

        team_region_list = [
            "eastus2",        # Team 1
            "northcentralus", # Team 2           (no longer used by SEG)
            "southcentralus", # Team 3
            "useast1",        # Team 4           (no longer used by SEG)
            "westus3",        # Team 5 (default)
        ]

        region_list = [
            "australiaeast",       #1
            "brazilsouth",         #2
            "canadaeast",          #3
            "eastus2",             #4 (SEG Team 1)
            "francecentral",       #5
            "germanywest-central", #6
            "japaneast",           #7
            "koreacentral",        #8
            "northcentralus",      #9
            "norwayeast",          #10
            "polandcentral",       #11
            "southafricanorth",    #12
            "southcentralus",      #13 (SEG Team 3)
            "southindia",          #14
            "swedencentral",       #15
            "switzerlandnorth",    #16
            "uksouth",             #17
            "useast1",             #18
            "westeurope",          #19
            "westus",              #20
            "westus3",             #21 (SEG Team 5) (SEG default)
        ]

        endpoint_list = [
            "https://psboai-advan-australiaeast-1.openai.azure.com/",    #1 australiaeast
            "https://psboai-brazilsouth.openai.azure.com/",              #2 brazilsouth
            "https://psboai-advan-canadaeast-1.openai.azure.com/",       #3 canadaeast
            "https://psboai-advan-eastus2-1.openai.azure.com/",          #4 eastus2
            "https://psboai-advan-francecentral-1.openai.azure.com/",    #5 francecentral
            "https://psboai-germanywest-central.openai.azure.com/",      #6 germanywest-central
            "https://psboai-advan-japaneast-1.openai.azure.com/",        #7 japaneast
            "https://psboai-koreacentral.openai.azure.com/",             #8 koreacentral
            "https://psboai-advan-northcentralus-1.openai.azure.com/",   #9 northcentralus
            "https://psboai-advan-norwayeast-1.openai.azure.com/",       #10 norwayeast
            "https://psboai-polandcentral.openai.azure.com/",            #11 polandcentral
            "https://psboai-advan-southafricanorth-1.openai.azure.com/", #12 southafricanorth
            "https://psboai-advan-southcentralus-1.openai.azure.com/",   #13 southcentralus
            "https://psboai-advan-southindia-1.openai.azure.com/",       #14 southindia
            "https://psboai-advan-swedencentral-1.openai.azure.com/",    #15 swedencentral
            "https://psboai-advan-switzerlandnorth-1.openai.azure.com/", #16 switzerlandnorth
            "https://psboai-advan-uksouth-1.openai.azure.com/",          #17 uksouth
            "https://psboai-advan-useast1-1.openai.azure.com/",          #18 useast1
            "https://psboai-advan-westeurope-1.openai.azure.com/",       #19 westeurope
            "https://psboai-advan-westus-1.openai.azure.com/",           #20 westus
            "https://psboai-advan-westus3-1.openai.azure.com/",          #21 westus3
        ]

        api_key_list = [
            "AZURE_AUSTRALIAEAST_KEY",
            "AZURE_BRAZILSOUTH_KEY",
            "AZURE_CANADAEAST_KEY",
            "AZURE_EASTUS2_KEY",
            "AZURE_FRANCECENTRAL_KEY",
            "AZURE_GERMANYWEST-CENTRAL_KEY",
            "AZURE_JAPANEAST_KEY",
            "AZURE_KOREACENTRAL_KEY",
            "AZURE_NORTHCENTRALUS_KEY",
            "AZURE_NORWAYEAST_KEY",
            "AZURE_POLANDCENTRAL_KEY",
            "AZURE_SOUTHAFRICANORTH_KEY",
            "AZURE_SOUTHCENTRALUS_KEY",
            "AZURE_SOUTHINDIA_KEY",
            "AZURE_SWEDENCENTRAL_KEY",
            "AZURE_SWITZERLANDNORTH_KEY",
            "AZURE_UKSOUTH_KEY",
            "AZURE_USEAST1_KEY",
            "AZURE_WESTEUROPE_KEY",
            "AZURE_WESTUS_KEY",
            "AZURE_WESTUS3_KEY",
        ]

        # # SEG VERSION #
        # default_team = "team5"
        # default_region = "westus3"
        # if region is None and team is None:
        #     team = default_team

        # If based on team
        if region is None:
            # Determine team index
            team = regex.sub(r"\s+", '', team).lower()
            if team in team_list:
                team_index = team_list.index(team)
            else:
                print(f"\n***ERROR*** set_azure_client() - team '{team}' not found!!\n")
                sys.exit()
                
                # # SEG VERSION #
                # team = default_team
                # team_index = team_list.index(team)
                
            # Determine corresponding region and region index
            team_region = team_region_list[team_index]
            if team_region in region_list:
                region_index = region_list.index(team_region)
            else:
                print(f"\n***ERROR*** set_azure_client() - region '{team_region}' not found!!\n")
                sys.exit()
                
                # # SEG VERSION #
                # team_region = default_region
                # region_index = region_list.index(team_region)
            region = team_region
            
            self.api_key = os.environ[api_key_list[region_index]]
            self.base = endpoint_list[region_index]
            
        # Else if based on region
        else:
            
            # Determine corresponding region and region index
            region = regex.sub(r"\s+", '', region).lower()
            
            # If 'azure' then find best available region
            if region == "azure":
                region = self.get_best_azure_region(api_model=model)
            elif region == "azure_advan":
                region = self.get_best_azure_region(api_model=model, advan=True)
            
            if region in region_list:
                region_index = region_list.index(region)
            else:
                print(f"\n***ERROR*** set_azure_client() - region '{region}' not found!!\n")
                sys.exit()
                
                # # SEG VERSION #
                # region = default_region
                # region_index = region_list.index(region)
            
            self.api_key = os.environ[api_key_list[region_index]]
            self.base = endpoint_list[region_index]
            
        # Determine correct model
        if model is None:
            model = "gpt-4o"
        elif model == "gpt-4-turbo":
            print("\n***ERROR*** set_azure_client() - gpt-4-turbo has been deprecated from Azure regions! Use 'openai' region if gpt-4-turbo is necessary.\n")
            sys.exit()
        
        # API Info
        self.team = team
        self.region = region
        self.model = model
        self.api_version = "2025-04-01-preview"
        
        # Set TPM/RPM, context window, and max output tokens
        self.set_openai_model_limits()
        


    def set_openai_model_limits(self):
        
        match self.model:
            
            ### gpt-5.6 ###
            case "gpt-5.6-sol":
                self.thinking = True
                self.tpm = 40_000_000
                self.rpm = 15_000
                self.context_window = 1_050_000
                self.max_output_tokens = 128_000
            case "gpt-5.6-terra":
                self.thinking = True
                self.tpm = 40_000_000
                self.rpm = 15_000
                self.context_window = 1_050_000
                self.max_output_tokens = 128_000
            case "gpt-5.6-luna":
                self.thinking = True
                self.tpm = 180_000_000
                self.rpm = 30_000
                self.context_window = 1_050_000
                self.max_output_tokens = 128_000
            
            ### gpt-5.5 ###
            case "gpt-5.5-pro" | "gpt-5.5-pro-2026-04-23":
                self.thinking = True
                self.tpm = 4_000_000
                self.rpm = 2_000
                self.context_window = 1_050_000
                self.max_output_tokens = 128_000
            case "gpt-5.5" | "gpt-5.5-2026-04-23":
                self.thinking = True
                self.tpm = 40_000_000
                self.rpm = 15_000
                self.context_window = 1_050_000
                self.max_output_tokens = 128_000
            
            ### gpt-5.4 ###
            case "gpt-5.4-pro" | "gpt-5.4-pro-2026-03-05":
                self.thinking = True
                self.tpm = 30_000_000
                self.rpm = 10_000
                self.context_window = 1_050_000
                self.max_output_tokens = 128_000
            case "gpt-5.4" | "gpt-5.4-2026-03-05":
                self.thinking = True
                self.tpm = 40_000_000
                self.rpm = 15_000
                self.context_window = 1_050_000
                self.max_output_tokens = 128_000
            case "gpt-5.4-mini" | "gpt-5.4-mini-2026-03-17":
                self.thinking = True
                self.tpm = 180_000_000
                self.rpm = 30_000
                self.context_window = 400_000
                self.max_output_tokens = 128_000
            case "gpt-5.4-nano" | "gpt-5.4-nano-2026-03-17":
                self.thinking = True
                self.tpm = 180_000_000
                self.rpm = 30_000
                self.context_window = 400_000
                self.max_output_tokens = 128_000
            
            ### gpt-5.2 ###
            case "gpt-5.2-pro" | "gpt-5.2-pro-2025-12-11":
                self.thinking = True
                self.tpm = 30_000_000
                self.rpm = 10_000
                self.context_window = 400_000
                self.max_output_tokens = 128_000
            case "gpt-5.2" | "gpt-5.2-2025-12-11":
                self.thinking = True
                self.tpm = 40_000_000
                self.rpm = 15_000
                self.context_window = 400_000
                self.max_output_tokens = 128_000
            
            ### gpt-5.1 ###
            case "gpt-5.1" | "gpt-5.1-2025-11-13":
                self.thinking = True
                self.tpm = 40_000_000
                self.rpm = 15_000
                self.context_window = 400_000
                self.max_output_tokens = 128_000
            
            ##### gpt-5 AND o3 MODELS WILL BE DEPRECATED ON DECEMBER 10TH, 2026 ######
            ### gpt-5 ###
            case "gpt-5-pro" | "gpt-5-pro-2025-10-06":
                self.thinking = True
                self.tpm = 30_000_000
                self.rpm = 10_000
                self.context_window = 400_000
                self.max_output_tokens = 272_000
            case "gpt-5" | "gpt-5-2025-08-07":
                self.thinking = True
                self.tpm = 40_000_000
                self.rpm = 15_000
                self.context_window = 400_000
                self.max_output_tokens = 128_000
            case "gpt-5-mini" | "gpt-5-mini-2025-08-07":
                self.thinking = True
                self.tpm = 180_000_000
                self.rpm = 30_000
                self.context_window = 400_000
                self.max_output_tokens = 128_000
            case "gpt-5-nano" | "gpt-5-nano-2025-08-07":
                self.thinking = True
                self.tpm = 180_000_000
                self.rpm = 30_000
                self.context_window = 400_000
                self.max_output_tokens = 128_000
            
            ##### gpt-5 AND o3 MODELS WILL BE DEPRECATED ON DECEMBER 10TH, 2026 ######
            ### o3 ###
            case "o3" | "o3-2025-04-16":
                self.thinking = True
                self.tpm = 30_000_000
                self.rpm = 10_000
                self.context_window = 200_000
                self.max_output_tokens = 100_000
            case "o3-pro" | "o3-pro-2025-06-10":
                self.thinking = True
                self.tpm = 30_000_000
                self.rpm = 10_000
                self.context_window = 200_000
                self.max_output_tokens = 100_000
            
            ### gpt-4.1 ###
            case "gpt-4.1" | "gpt-4.1-2025-04-14":
                self.thinking = False
                self.tpm = 30_000_000
                self.rpm = 10_000
                self.context_window = 1_047_576
                self.max_output_tokens = 32_768
            case "gpt-4.1-mini" | "gpt-4.1-mini-2025-04-14":
                self.thinking = False
                self.tpm = 150_000_000
                self.rpm = 30_000
                self.context_window = 1_047_576
                self.max_output_tokens = 32_768
            
            ### gpt-4o ###
            case "gpt-4o" | "gpt-4o-s" | "gpt-4o-2024-08-06" | "gpt-4o-2024-11-20":
                self.thinking = False
                self.tpm = 150_000_000
                self.rpm = 50_000
                self.context_window = 128_000
                self.max_output_tokens = 16_384
            case "gpt-4o-mini" | "gpt-4o-mini-2024-07-18":
                self.thinking = False
                self.tpm = 150_000_000
                self.rpm = 50_000
                self.context_window = 128_000
                self.max_output_tokens = 16_384
            
            ### no match for model ###
            case _:
                print(f"\n***ERROR*** set_limits() - The model '{self.model}' is not an approved model!\n")
                sys.exit()


    def set_vertex_model_limits(self):
        
        match self.model:
            
            ### Vertex uses Dynamic shared quota (DSQ) ###
            ### https://cloud.google.com/vertex-ai/generative-ai/docs/dynamic-shared-quota ###
            ### Because of this, I've set the TPMs and RPMs high at something similar to OpenAI ###
            
            ### 3.6 ###
            case "gemini-3.6-flash":
                self.thinking = True
                self.tpm = 100_000_000
                self.rpm = 5_000
                self.context_window = 1_048_576
                self.max_output_tokens = 65_536
            
            ### 3.5 ###
            case "gemini-3.5-flash":
                self.thinking = True
                self.tpm = 100_000_000
                self.rpm = 5_000
                self.context_window = 1_048_576
                self.max_output_tokens = 65_536
            
            ### 3.1 ###
            case "gemini-3.1-pro-preview":
                self.thinking = True
                self.tpm = 100_000_000
                self.rpm = 5_000
                self.context_window = 1_048_576
                self.max_output_tokens = 65_536
            case "gemini-3.1-flash-lite" | "gemini-3.1-flash-lite-preview":
                self.thinking = True
                self.tpm = 100_000_000
                self.rpm = 5_000
                self.context_window = 1_048_576
                self.max_output_tokens = 65_536
            
            ### 3.0 ###
            case "gemini-3-flash-preview":
                self.thinking = True
                self.tpm = 100_000_000
                self.rpm = 5_000
                self.context_window = 1_048_576
                self.max_output_tokens = 65_536
            
            ### 2.5 ###
            case "gemini-2.5-pro":
                self.thinking = True
                self.tpm = 100_000_000
                self.rpm = 5_000
                self.context_window = 1_048_576
                self.max_output_tokens = 65_535
            case "gemini-2.5-flash":
                self.thinking = True
                self.tpm = 100_000_000
                self.rpm = 5_000
                self.context_window = 1_048_576
                self.max_output_tokens = 65_535
            case "gemini-2.5-flash-lite":
                self.thinking = True
                self.tpm = 100_000_000
                self.rpm = 5_000
                self.context_window = 1_048_576
                self.max_output_tokens = 65_535
            
            ### 2.0 ###
            # case "gemini-2.0-flash":
            #     self.thinking = False
            #     self.tpm = 100_000_000
            #     self.rpm = 15_000
            #     self.context_window = 1_048_576
            #     self.max_output_tokens = 8_192
            # case "gemini-2.0-flash-lite":
            #     self.thinking = False
            #     self.tpm = 100_000_000
            #     self.rpm = 15_000
            #     self.context_window = 1_048_576
            #     self.max_output_tokens = 8_192
            
            ### Llama 4 ###
            case "meta/llama-4-maverick-17b-128e-instruct-maas":
                self.thinking = False
                self.tpm = 30_000_000 # could not find documentation for TPM
                self.rpm = 5_000
                self.context_window = 524_288
                self.max_output_tokens = 8_192
            
            ### no match for model ###
            case _:
                print(f"\n***ERROR*** set_limits() - The model '{self.model}' is not an approved model!\n")
                sys.exit()
            
    
    def create_api_client(
            self,
    ):
        match self.base_type:
            
            case "openai":
                return OpenAI(api_key = self.api_key, max_retries=5)
            
            case "vertex":
                return genai.Client(
                    vertexai=True,
                    project="psb-vertex-testing",
                    location=self.region,
                )
            
            case "meta_vertex":
                project_id = "psb-vertex-testing"
                return OpenAI(
                    base_url=f"https://{self.region}-aiplatform.googleapis.com/v1beta1/projects/{project_id}/locations/{self.region}/endpoints/openapi",
                    api_key=self.api_key,
                    max_retries=5,
                )
            
            case "azure":
                return AzureOpenAI(
                    api_key = self.api_key,
                    azure_endpoint = self.base,
                    api_version = self.api_version,
                )
    
    @asynccontextmanager
    async def create_async_api_client(
            self,
    ):
        match self.base_type:
            
            case "openai":
                async with AsyncOpenAI(api_key=self.api_key, max_retries=5) as client:
                    yield client
                    
            case "vertex":
                with genai.Client(
                    vertexai=True,
                    project="psb-vertex-testing",
                    location=self.region,
                ) as client:
                    yield client
            
            case "meta_vertex":
                project_id = "psb-vertex-testing"
                async with AsyncOpenAI(
                    base_url=f"https://{self.region}-aiplatform.googleapis.com/v1beta1/projects/{project_id}/locations/{self.region}/endpoints/openapi",
                    api_key=self.api_key,
                    max_retries=5,
                ) as client:
                    yield client
            
            case "azure":
                async with AsyncAzureOpenAI(
                    api_key = self.api_key,
                    azure_endpoint = self.base,
                    api_version = self.api_version,
                ) as client:
                    yield client
    
    
    def check_model_details(
            self,
            model_details: list[dict],
            batch: bool = False,
    ) -> list[dict]:
        
        if type(model_details) is not list:
            model_details = [model_details]
        
        if len(model_details) == 0:
            print("\n***ERROR*** check_model_details() - model details not found!\n")
            sys.exit()
        
        for i in range(len(model_details)):
            m = model_details[i]
            
            if 'type' in m:
                if m['type'] not in ["openai", "azure", "vertex", "meta_vertex"]:
                    print(f"\n***ERROR*** check_model_details() - '{m['type']}' is not a valid model type!\n")
                    sys.exit()
            else:
                model_details[i]['type'] = "openai"
            
            if 'region' in m:
                match model_details[i]['type']:
                    case "openai":
                        model_details[i]['region'] = "openai"
            else:
                match model_details[i]['type']:
                    case "openai":
                        model_details[i]['region'] = "openai"
                    case "azure":
                        model_details[i]['region'] = "azure"
                    case "vertex":
                        model_details[i]['region'] = "global"
                    case "meta_vertex":
                        model_details[i]['region'] = "us-east5"
            
            if 'model' not in m:
                match model_details[i]['type']:
                    case "openai" |"azure":
                        model_details[i]['model'] = "gpt-4o"
                    case "vertex":
                        model_details[i]['model'] = "gemini-2.5-flash"
                    case "meta_vertex":
                        model_details[i]['model'] = "meta/llama-4-maverick-17b-128e-instruct-maas"
            
            if 'reasoning' in m:
                model_details[i]['reasoning'] = str(model_details[i]['reasoning'])
            else:
                match model_details[i]['type']:
                    case "openai" | "azure":
                        model_details[i]['reasoning'] = "medium"
                    case "vertex":
                        model_details[i]['reasoning'] = "-1"
                    case "meta_vertex":
                        model_details[i]['reasoning'] = "None"
                
            
            if 'temp' in m and m['temp'] is not None:
                model_details[i]['temp'] = float(model_details[i]['temp'])
            else:
                model_details[i]['temp'] = None
                
            if 'max_output' in m and m['max_output'] is not None:
                model_details[i]['max_output'] = int(model_details[i]['max_output'])
            else:
                model_details[i]['max_output'] = None
            
            if 'top_p' in m and m['top_p'] is not None:
                model_details[i]['top_p'] = float(model_details[i]['top_p'])
            else:
                model_details[i]['top_p'] = None
            
            if 'web_search' in m and m['web_search'] is not None:
                model_details[i]['web_search'] = bool(model_details[i]['web_search'])
            else:
                model_details[i]['web_search'] = False
                
        return(model_details)
    
    
    def check_model_minimums(
            self,
            model_details: list[dict],
    ):
        # Reset mins
        self.model_minimums = {
            "context_window": None,
            "max_output_tokens": None,
        }
        
        # Loop over models to find mins
        for model in model_details:
            self.set_client(model_details=model)
            
            # Context window
            if self.model_minimums['context_window'] is None:
                self.model_minimums['context_window'] = self.context_window
            elif self.context_window < self.model_minimums['context_window']:
                self.model_minimums['context_window'] = self.context_window
                
            # Max output tokens
            if self.model_minimums['max_output_tokens'] is None:
                self.model_minimums['max_output_tokens'] = self.max_output_tokens
            elif self.max_output_tokens < self.model_minimums['max_output_tokens']:
                self.model_minimums['max_output_tokens'] = self.max_output_tokens
        
    
    def estimate_tokens(self, prompt: dict) -> int:
        """Estimate the number of tokens in the prompt"""
        
        if prompt is None:
            return 0
        
        num_tokens = 0
        
        try:
            encoding = tiktoken.encoding_for_model(self.model)
        except:
            encoding = tiktoken.encoding_for_model("gpt-4o")                
        for message in prompt:
            num_tokens += 4  # every message follows <im_start>{role/name}\n{content}<im_end>\n
            if message is not None and isinstance(message, dict):
                for key, value in message.items():
                    num_tokens += len(encoding.encode(value))
                    # if key == "name":  # if there's a name, the role is omitted
                    #     num_tokens -= 1  # role is always required and always 1 token
        num_tokens += 2  # every reply is primed with <im_start>assistant
        
        # match self.base_type:
            
        #     case "openai" | "azure":
        #         try:
        #             encoding = tiktoken.encoding_for_model(self.model)
        #         except:
        #             encoding = tiktoken.encoding_for_model("gpt-4o")                
        #         for message in prompt:
        #             num_tokens += 4  # every message follows <im_start>{role/name}\n{content}<im_end>\n
        #             if message is not None and isinstance(message, dict):
        #                 for key, value in message.items():
        #                     num_tokens += len(encoding.encode(value))
        #                     # if key == "name":  # if there's a name, the role is omitted
        #                     #     num_tokens -= 1  # role is always required and always 1 token
        #         num_tokens += 2  # every reply is primed with <im_start>assistant
                
        #     case "vertex":
        #         for message in prompt:
        #             num_tokens += 4
        #             if message is not None and isinstance(message, dict):
        #                 for key, value in message.items():
        #                     token_counts = self.client.models.count_tokens(model=self.model, contents=value)
        #                     num_tokens += token_counts.total_tokens
        #         num_tokens += 2
        
        return num_tokens
    
    
    def estimate_costs(
            self,
            display: bool = True,
    ) -> tuple[float, pd.DataFrame]:
        """Estimates the total cost of tokens in+out, based on current model and all saved stats from status list"""
        
        if display:
            print("\nEstimating Costs:\n------------")
        total_cost = 0
        total_tin = 0
        total_tout = 0
        total_ct = 0
        total_rt = 0
        for i in range(len(self.status_list)):
            total_cost += self.status_list[i].total_cost
            total_tin += self.status_list[i].tokens_in
            total_tout += self.status_list[i].tokens_out
            total_ct += self.status_list[i].cached_tokens_in
            total_rt += self.status_list[i].reasoning_tokens_out
        out_df = pd.DataFrame({
            "set": [self.status_list[item].set_name if self.status_list[item].set_name != "" else f"Set {item+1}" for item in range(len(self.status_list))],
            "model": [item.model for item in self.status_list],
            #"cost": ['${:,.4f}'.format(item.total_cost) for item in self.status_list],
            "cost": [item.total_cost for item in self.status_list],
            "tokens in": [item.tokens_in for item in self.status_list],
            "tokens out": [item.tokens_out for item in self.status_list],
            "cached tokens": [item.cached_tokens_in for item in self.status_list],
            "reasoning tokens": [item.reasoning_tokens_out for item in self.status_list],
        })
        #out_df.loc[len(out_df)] = ["Total", "", '${:,.4f}'.format(total_cost), total_tin, total_tout, total_ct, total_rt]
        out_df.loc[len(out_df)] = ["Total", "", total_cost, total_tin, total_tout, total_ct, total_rt]
        if display:
            print(out_df[["set","model","cost"]].to_markdown(index = False) + "\n")
        return total_cost, out_df
    
    
    def output_costs_to_sheet(
            self,
            writer: pd.ExcelWriter,
            cost_df: pd.DataFrame,
            sheet_name: str = "Costs",
    ) -> None:
        cost_df.to_excel(writer, sheet_name = sheet_name, header = True, index = False)
        writer.sheets[sheet_name].column_dimensions['B'].width = 50
        for i in range(len(cost_df.index)):
            writer.sheets[sheet_name]['C' + str(i+2)].number_format = "$0.0000"
        writer.sheets[sheet_name].column_dimensions['A'].width = 30
        writer.sheets[sheet_name].column_dimensions['B'].width = 40
        writer.sheets[sheet_name].column_dimensions['D'].width = 18
        writer.sheets[sheet_name].column_dimensions['E'].width = 18
        writer.sheets[sheet_name].column_dimensions['F'].width = 18
        writer.sheets[sheet_name].column_dimensions['G'].width = 18


    def output_costs_to_excel(
            self,
            out_path: pathlib.Path,
            cost_df: pd.DataFrame,
    ) -> None:
        with pd.ExcelWriter(out_path, engine='openpyxl') as writer:
            self.output_costs_to_sheet(writer=writer, cost_df=cost_df)
    
    
    def api_info_string(
            self,
            set_name: str,
    ) -> str:
        
        if (self.base_type == "openai"):
            info_string = f" (type=OpenAI, model={self.model})"
        elif (self.base_type == "vertex"):
            info_string = f" (type=Vertex, region={self.region}, model={self.model})"
        elif (self.base_type == "meta_vertex"):
            info_string = f" (type=Meta-Vertex, region={self.region}, model={self.model})"
        elif (self.base_type == "azure"):
            info_string = f" (type=Azure, region={self.region}, model={self.model})"
        if (set_name is not None):
            info_string = f" for set '{set_name}'\n\t" + info_string
            
        return info_string
    
    
    def time_elapsed_string(self, start_time: float) -> str:
        
        time_elapsed = time.time() - start_time
        time_elapsed_min = int(time_elapsed // 60)
        time_elapsed_sec = round(time_elapsed % 60, 2)    
        return f"{time_elapsed_min} min. {time_elapsed_sec} sec."
    
    
    def get_limits(self):
        
        # Create temporary APIRequest object
        api_request = APIRequest(
            task_id=0,
            model_name=self.model,
            messages={},
        )
        
        # Ping API for limits
        api_request.get_api_limits(
            api_client=self.client,
            model_name=self.model,
            status_tracker=self.status,
        )


    def get_best_azure_region(
            self,
            api_model: str,
            advan: bool = False,
    ) -> str:
        
        #print("\n***Choosing Best Azure Region***")
        
        regions_to_check = ["swedencentral","useast1","westus"] # temporarily removed "northcentralus" until Azure makes Responses API available for it
        if (advan):
            regions_to_check = ["australiaeast","norwayeast","southindia","uksouth"]
        
        num_regions = len(regions_to_check)
        tpm_list = np.array([0] * num_regions)
        rpm_list = np.array([0] * num_regions)
        for i in range(num_regions):
            
            # current region
            region = regions_to_check[i]
            
            # ping API
            self.set_azure_client(region=region, model=api_model) 
            self.update_client(parallel=False)
            self.status = APIStatus()
            self.get_limits()
            
            # save limits
            tpm_list[i] = self.status.available_tpm
            rpm_list[i] = self.status.available_rpm
        
        # determine index of max values
        max_tpm = max(tpm_list)
        max_rpm = max(rpm_list[tpm_list == max_tpm])
        final_indices = np.where((tpm_list == max_tpm) & (rpm_list == max_rpm))
        final_index = final_indices[0].tolist()[0]
        
        # # print findings
        # for i in range(num_regions):
        #     if (i == final_index):
        #         print(f"{regions_to_check[i]} ({tpm_list[i]}/{rpm_list[i]}) <-----")
        #     else:
        #         print(f"{regions_to_check[i]} ({tpm_list[i]}/{rpm_list[i]})")
        
        return(regions_to_check[final_index])
    

    def create_request(
            self,
            task_id: int,
            prompt: str,
            user_tokens: int,
            max_tokens: list[int],
            num_max_tokens: int,
            temperature: list[float],
            num_temps: int,
            top_p: list[float],
            num_top_ps: int,
            attempts_left: int,
    ) -> APIRequest:
        
        # Determine settings for new request
        if (task_id < num_max_tokens):
            next_max_tokens = max_tokens[task_id]
        else:
            next_max_tokens = max_tokens[-1]
        if (task_id < num_temps):
            next_temp = temperature[task_id]
        else:
            next_temp = temperature[-1]
        if (task_id < num_top_ps):
            next_top_p = top_p[task_id]
        else:
            next_top_p = top_p[-1]
        
        # Check max_tokens
        if (next_max_tokens is None or next_max_tokens > self.max_output_tokens):
            next_max_tokens = self.max_output_tokens
        
        # Return new request
        return(APIRequest(
            task_id=task_id,
            model_name=self.model,
            messages=prompt,
            max_tokens=next_max_tokens,
            attempts_left=attempts_left,
            user_tokens=user_tokens,            
            temperature=next_temp,
            top_p=next_top_p
        ))


    async def run(
        self,
        prompts: list[dict],
        response_format: list[dict] = None,
        api_file_parts: list[dict] = None,
        max_tokens: list[int] = None,
        temperature: list[float] = None,
        top_p: list[float] = None,
        reasoning_effort: str = None,
        web_search: bool = False,
        set_name: str = None,
        silent: bool = False,
    ):
        """Processes API requests in serial. One at a time."""
        
        api_start_time = time.time()
        set_string = self.api_info_string(set_name=set_name)
        logger.info("Starting API calls" + set_string)
        if silent == False:
            print("\n\tAPI calls" + set_string + ":")
        
        # Check prompts
        for i in range(len(prompts)):
            if prompts[i] is not None:
                if not isinstance(prompts[i], list):
                    prompts = [prompts]
                break
        
        # Check response format
        if response_format is None:
            response_format = [None]
        elif type(response_format) is not list:
            response_format = [response_format]
        if len(response_format) < len(prompts): # expand formats to same length as prompts
            response_format = response_format + [response_format[-1]] * (len(prompts) - len(response_format))
        
        # Check file parts
        if api_file_parts is None:
            api_file_parts = [None]
        elif type(api_file_parts) is not list:
            api_file_parts = [api_file_parts]
        if len(api_file_parts) < len(prompts): # expand parts to same length as prompts
            api_file_parts = api_file_parts + [api_file_parts[-1]] * (len(prompts) - len(api_file_parts))
        
        # Check max_tokens
        if max_tokens is None:
            max_tokens = [None]
        elif type(max_tokens) is not list:
            max_tokens = [max_tokens]
        
        # Check temperature
        if temperature is None:
            temperature = [None]
        elif type(temperature) is not list:
            temperature = [temperature]
        
        # Check top_p
        if top_p is None:
            top_p = [None]
        elif type(top_p) is not list:
            top_p = [top_p]
        
        # Input length
        num_max_tokens = len(max_tokens)
        num_temps = len(temperature)
        num_top_ps = len(top_p)
        max_option_length = max([
            num_max_tokens,
            num_temps,
            num_top_ps
        ])
        
        # Check for duplicate prompts
        has_dupes = False
        if max_option_length == 1:
            prompts_deduped = []
            response_format_deduped = []
            api_file_parts_deduped = []
            seen_prompt_idx = {}
            prompt_index_map = []
            for i in range(len(prompts)):
                p = prompts[i]
                rf = response_format[i]
                afp = api_file_parts[i]
                prf = []
                if p is not None: prf = p
                if rf is not None: prf = prf + [rf]
                if afp is not None: prf = prf + [afp]
                prompt_key = json.dumps(prf, sort_keys=True)
                if prompt_key not in seen_prompt_idx:
                    seen_prompt_idx[prompt_key] = len(prompts_deduped)
                    prompts_deduped.append(p)
                    response_format_deduped.append(rf)
                    api_file_parts_deduped.append(afp)
                prompt_index_map.append(seen_prompt_idx[prompt_key])
            if len(prompts_deduped) < len(prompts):
                has_dupes = True
                logger.info((
                    f"*** Duplicates detected, magically reducing request count from {len(prompts)}"
                    f" to {len(prompts_deduped)}, just for you :) ***"
                ))
        else:
            prompts_deduped = deepcopy(prompts)
            response_format_deduped = deepcopy(response_format)
            api_file_parts_deduped = deepcopy(api_file_parts)
        
        # Initialize status tracker
        num_prompts = len(prompts_deduped)
        self.status = APIStatus()
        self.status.model = self.model
        self.status.set_name = set_name
        self.status.num_tasks = num_prompts
        self.status.max_tpm = self.tpm
        self.status.max_rpm = self.rpm
        self.status.calculated_tpm = self.tpm
        self.status.calculated_rpm = self.rpm
        self.status.tokens_in = 0
        self.status.cached_tokens_in = 0
        self.status.tokens_out = 0
        self.status.reasoning_tokens_out = 0
        
        # Ping API to get initial TPM/RPM limits
        #self.get_limits()
        
        # Initialize data structures to save chat results
        self.results = [""]*num_prompts
        self.status.completed_tasks = np.array([False] * num_prompts)
        
        # Count input tokens
        input_token_counts = [self.estimate_tokens(item) for item in prompts_deduped]
        
        # Begin API requests
        with self.create_api_client() as api_client:
            
            last_time = time.time()
            for i in range(num_prompts):
                
                # Create new APIRequest object
                api_request = self.create_request(
                        task_id=i,
                        prompt=prompts_deduped[i],
                        user_tokens=input_token_counts[i],
                        max_tokens=max_tokens,
                        num_max_tokens=num_max_tokens,
                        temperature=temperature,
                        num_temps=num_temps,
                        top_p=top_p,
                        num_top_ps=num_top_ps,
                        attempts_left=self.max_attempts,
                )
                self.status.num_tasks_started += 1
                self.status.num_tasks_in_progress += 1
                
                # Update available capacities
                current_time = time.time()
                seconds_since_last = current_time - last_time
                self.status.calculated_tpm = min(
                    self.status.max_tpm,
                    self.status.calculated_tpm + (self.status.max_tpm * seconds_since_last / 60.0),
                )
                self.status.calculated_rpm = min(
                    self.status.max_rpm,
                    self.status.calculated_rpm + (self.status.max_rpm * seconds_since_last / 60.0),
                )
                last_time = current_time
                
                # Call API
                match self.base_type:
                    case "openai" | "azure":
                        while (api_request.success == False and api_request.attempts_left >= 0):
                            api_request.call_openai_api(
                                api_client=api_client,
                                model_name=self.model,
                                status_tracker=self.status,
                                result_list=self.results,
                                response_format=None if response_format_deduped[i] is None else response_format_deduped[i]['openai'],
                                api_file_parts=None if api_file_parts_deduped[i] is None else api_file_parts_deduped[i]['openai'],
                                thinking=self.thinking,
                                reasoning_effort=reasoning_effort,
                                web_search=web_search,
                            )
                            
                            if api_request.success == False:
                                api_request.attempts_left -= 1
                                time.sleep(self.seconds_to_pause)
                            
                    case "vertex":
                        while (api_request.success == False and api_request.attempts_left >= 0):
                            api_request.call_vertex_api(
                                api_client=api_client,
                                model_name=self.model,
                                status_tracker=self.status,
                                result_list=self.results,
                                response_format=None if response_format_deduped[i] is None else response_format_deduped[i]['vertex'],
                                api_file_parts=None if api_file_parts_deduped[i] is None else api_file_parts_deduped[i]['vertex'],
                                thinking=self.thinking,
                                reasoning_effort=reasoning_effort,
                                web_search=web_search,
                            )
                            
                            if api_request.success == False:
                                api_request.attempts_left -= 1
                                time.sleep(self.seconds_to_pause)
                                
                    case "meta_vertex":
                        while (api_request.success == False and api_request.attempts_left >= 0):
                            api_request.call_meta_vertex_api(
                                api_client=api_client,
                                model_name=self.model,
                                status_tracker=self.status,
                                result_list=self.results,
                                response_format=None if response_format_deduped[i] is None else response_format_deduped[i]['openai'],
                                api_file_parts=None if api_file_parts_deduped[i] is None else api_file_parts_deduped[i]['openai'],
                                thinking=self.thinking,
                                reasoning_effort=reasoning_effort,
                                web_search=web_search,
                            )
                            
                            if api_request.success == False:
                                api_request.attempts_left -= 1
                                time.sleep(self.seconds_to_pause)
                
                # Update user
                if silent == False:
                    self.status.print_progress(has_dupes=has_dupes)
            
        # If duplicates, map results back to correct positions
        if has_dupes == True:
            results_deduped = deepcopy(self.results)
            self.results = [results_deduped[item] for item in prompt_index_map]
        
        # Save stats from this run to list
        self.status_list.append(self.status)
        
        # Print time taken
        time_elapsed = self.time_elapsed_string(api_start_time)
        logger.info("Finished API calls" + set_string)
        logger.info(f"\t{time_elapsed}")
        logger.info((
            f"\tTokens In = {self.status.tokens_in} (Cached = {self.status.cached_tokens_in})"
            f"\n\tTokens Out = {self.status.tokens_out} (Reasoning = {self.status.reasoning_tokens_out})"
        ))
        if silent == False:
            print("")
            print(f"\t{time_elapsed}")
            print((
                f"\tTokens In = {self.status.tokens_in} (Cached = {self.status.cached_tokens_in})"
                f"\n\tTokens Out = {self.status.tokens_out} (Reasoning = {self.status.reasoning_tokens_out})"
            ))


    @staticmethod
    def create_tracked_task(task_set: set, coro):
        """Create a task and keep a strong reference until it finishes."""

        task = asyncio.create_task(coro)
        task_set.add(task)
        task.add_done_callback(task_set.discard)
        return task
    
    
    async def wait_for_tracked_tasks(task_set: set):
        """Cancel sibling tasks on failure and drain all tasks before exit."""

        tasks = tuple(task_set)
        if len(tasks) == 0:
            return

        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
    
    
    async def run_parallel(
        self,
        prompts: list[dict],
        response_format: list[dict] = None,
        api_file_parts: list[dict] = None,
        max_tokens: list[int] = None,
        temperature: list[float] = None,
        top_p: list[float] = None,
        reasoning_effort: str = None,
        web_search: bool = False,
        timeout: int = 500,
        final_timeout: int = 120,
        set_name: str = None,
        max_parallel: int = None,
        silent: bool = False,
    ):
        """Processes API requests in parallel, throttling to stay under rate limits."""
        
        api_start_time = time.time()
        set_string = self.api_info_string(set_name=set_name)
        logger.info("Starting API calls" + set_string)
        if silent == False:
            print("\n\tAPI calls" + set_string + ":")
        
        # Check prompts
        for i in range(len(prompts)):
            if prompts[i] is not None:
                if not isinstance(prompts[i], list):
                    prompts = [prompts]
                break
        
        # Check response format
        if response_format is None:
            response_format = [None]
        elif type(response_format) is not list:
            response_format = [response_format]
        if len(response_format) < len(prompts): # expand formats to same length as prompts
            response_format = response_format + [response_format[-1]] * (len(prompts) - len(response_format))
        
        # Check file parts
        if api_file_parts is None:
            api_file_parts = [None]
        elif type(api_file_parts) is not list:
            api_file_parts = [api_file_parts]
        if len(api_file_parts) < len(prompts): # expand parts to same length as prompts
            api_file_parts = api_file_parts + [api_file_parts[-1]] * (len(prompts) - len(api_file_parts))
        
        # Check max_tokens
        if max_tokens is None:
            max_tokens = [None]
        elif type(max_tokens) is not list:
            max_tokens = [max_tokens]
        
        # Check temperature
        if temperature is None:
            temperature = [None]
        elif type(temperature) is not list:
            temperature = [temperature]
        
        # Check top_p
        if top_p is None:
            top_p = [None]
        elif type(top_p) is not list:
            top_p = [top_p]
        
        # Check max_parallel
        if max_parallel is None:
            max_parallel = self.max_parallel
        
        # Input length
        num_max_tokens = len(max_tokens)
        num_temps = len(temperature)
        num_top_ps = len(top_p)
        max_option_length = max([
            num_max_tokens,
            num_temps,
            num_top_ps
        ])
        
        # Increase timeouts for thinking models
        if self.thinking == True and not(self.base_type == "vertex" and reasoning_effort == "0"):
            timeout += 500
            final_timeout += 120
        
        # Generators
        task_id_generator = self.task_id_generator_function()
        
        # Check for duplicate prompts
        has_dupes = False
        if max_option_length == 1:
            prompts_deduped = []
            response_format_deduped = []
            api_file_parts_deduped = []
            seen_prompt_idx = {}
            prompt_index_map = []
            for i in range(len(prompts)):
                p = prompts[i]
                rf = response_format[i]
                afp = api_file_parts[i]
                prf = []
                if p is not None: prf = p
                if rf is not None: prf = prf + [rf]
                if afp is not None: prf = prf + [afp]
                prompt_key = json.dumps(prf, sort_keys=True)
                if prompt_key not in seen_prompt_idx:
                    seen_prompt_idx[prompt_key] = len(prompts_deduped)
                    prompts_deduped.append(p)
                    response_format_deduped.append(rf)
                    api_file_parts_deduped.append(afp)
                prompt_index_map.append(seen_prompt_idx[prompt_key])
            if len(prompts_deduped) < len(prompts):
                has_dupes = True
                logger.info((
                    f"*** Duplicates detected, magically reducing request count from {len(prompts)}"
                    f" to {len(prompts_deduped)}, just for you :) ***"
                ))
        else:
            prompts_deduped = deepcopy(prompts)
            response_format_deduped = deepcopy(response_format)
            api_file_parts_deduped = deepcopy(api_file_parts)
        
        # Initialize status tracker
        num_prompts = len(prompts_deduped)
        self.status = APIStatus()
        self.status.model = self.model
        self.status.set_name = set_name
        self.status.num_tasks = num_prompts
        self.status.max_tpm = self.tpm
        self.status.max_rpm = self.rpm
        self.status.calculated_tpm = self.tpm
        self.status.calculated_rpm = self.rpm
        
        # Ping API to get initial TPM/RPM limits
        #self.update_client(parallel=False)
        #self.get_limits()
        
        # Initialize seconds to sleep in loop
        seconds_to_sleep_each_loop = 1 / (self.status.max_rpm / 60)
        
        # Initialize data structures to save chat results
        self.results = [""]*num_prompts
        
        # Count input tokens
        input_token_counts = [self.estimate_tokens(item) for item in prompts_deduped]
        
        # Begin API requests
        last_time = time.time()
        next_request = None
        queue_of_requests_to_retry = asyncio.Queue()
        self.status.completed_tasks = np.array([False] * num_prompts)
        
        active_tasks = set()
        task_group = None
        use_task_group = False
        
        async with self.create_async_api_client() as api_client:
            
            try:
                task_group = asyncio.TaskGroup()
                await task_group.__aenter__()
                use_task_group = True
            except RuntimeError as e:
                logger.warning(
                    "TaskGroup unavailable in current event loop; falling back to tracked asyncio tasks: %s",
                    e,
                )

            try:
                while True:
                    # Get next request (if one is not already waiting for capacity)
                    if next_request is None:
                        
                        if not queue_of_requests_to_retry.empty():                
                            next_request = queue_of_requests_to_retry.get_nowait()
                            logger.info(f"Retrying task #{next_request.task_id}: {next_request}")
                            #print(f"\nRetrying task #{next_request.task_id}")
                            
                        else:
                            task_id = next(task_id_generator)
                            if task_id < num_prompts:
                                
                                # Create new APIRequest object
                                next_request = self.create_request(
                                        task_id=task_id,
                                        prompt=prompts_deduped[task_id],
                                        user_tokens=input_token_counts[task_id],
                                        max_tokens=max_tokens,
                                        num_max_tokens=num_max_tokens,
                                        temperature=temperature,
                                        num_temps=num_temps,
                                        top_p=top_p,
                                        num_top_ps=num_top_ps,
                                        attempts_left=self.max_attempts,
                                )
                                
                                self.status.num_tasks_started += 1
                                self.status.num_tasks_in_progress += 1
                                logger.info(f"Start of task #{next_request.task_id}")
                                #print(f"\rStart of task #{next_request.task_id}",end="\r",flush=True)
                    
                    # Update available capacities
                    current_time = time.time()
                    seconds_since_last = current_time - last_time
                    self.status.calculated_tpm = min(
                        self.status.max_tpm,
                        self.status.calculated_tpm + (self.status.max_tpm * seconds_since_last / 60.0),
                    )
                    self.status.calculated_rpm = min(
                        self.status.max_rpm,
                        self.status.calculated_rpm + (self.status.max_rpm * seconds_since_last / 60.0),
                    )
                    last_time = current_time
                    
                    # Adjust seconds to sleep
                    if (self.status.last_request_tokens != 0):
                        seconds_to_sleep_each_loop = max(
                            60 / ((self.status.calculated_tpm * 0.95) / self.status.last_request_tokens),
                            1 / ((self.status.calculated_rpm * 0.95) / 60),
                        )
                    else:
                        seconds_to_sleep_each_loop = 1 / ((self.status.calculated_rpm * 0.95) / 60)
                    
                    if next_request:
                        # If enough capacity available, call API
                        if (
                            self.status.calculated_rpm >= 1
                            and self.status.calculated_tpm >= next_request.max_possible_tokens
                            and self.status.num_tasks_in_progress <= max_parallel
                        ):
                            # Update counters
                            self.status.calculated_tpm -= next_request.max_possible_tokens
                            self.status.calculated_rpm -= 1
                            next_request.attempts_left -= 1
                            
                            # Create sub-task, which calls API
                            match self.base_type:
                                case "openai" | "azure":
                                    request_coro = next_request.call_openai_api_parallel(
                                        api_client=api_client,
                                        model_name=self.model,
                                        status_tracker=self.status,
                                        retry_queue=queue_of_requests_to_retry,
                                        result_list=self.results,
                                        timeout=timeout,
                                        response_format=None if response_format_deduped[next_request.task_id] is None else response_format_deduped[next_request.task_id]['openai'],
                                        api_file_parts=None if api_file_parts_deduped[next_request.task_id] is None else api_file_parts_deduped[next_request.task_id]['openai'],
                                        thinking=self.thinking,
                                        reasoning_effort=reasoning_effort,
                                        web_search=web_search,
                                    )
                                    
                                case "vertex":
                                    request_coro = next_request.call_vertex_api_parallel(
                                        api_client=api_client,
                                        model_name=self.model,                                    
                                        status_tracker=self.status,
                                        retry_queue=queue_of_requests_to_retry,
                                        result_list=self.results,
                                        timeout=timeout,
                                        response_format=None if response_format_deduped[next_request.task_id] is None else response_format_deduped[next_request.task_id]['vertex'],
                                        api_file_parts=None if api_file_parts_deduped[next_request.task_id] is None else api_file_parts_deduped[next_request.task_id]['vertex'],
                                        thinking=self.thinking,
                                        reasoning_effort=reasoning_effort,
                                        web_search=web_search,
                                    )
                                    
                                case "meta_vertex":
                                    request_coro = next_request.call_meta_vertex_api_parallel(
                                        api_client=api_client,
                                        model_name=self.model,
                                        status_tracker=self.status,
                                        retry_queue=queue_of_requests_to_retry,
                                        result_list=self.results,
                                        timeout=timeout,
                                        response_format=None if response_format_deduped[next_request.task_id] is None else response_format_deduped[next_request.task_id]['openai'],
                                        api_file_parts=None if api_file_parts_deduped[next_request.task_id] is None else api_file_parts_deduped[next_request.task_id]['openai'],
                                        thinking=self.thinking,
                                        reasoning_effort=reasoning_effort,
                                        web_search=web_search,
                                    )
                            
                            if use_task_group == True:
                                task_group.create_task(request_coro)
                            else:
                                self.create_tracked_task(active_tasks, request_coro)
                            
                            # Reset next_request to empty
                            next_request = None  
                            self.status.last_activity_time = time.time()
                            
                    # Update user
                    if silent == False:
                        self.status.print_progress(has_dupes=has_dupes)
                    
                    # If all tasks are finished, break
                    if self.status.num_tasks_in_progress == 0:
                        break
            
                    # Are any requests unresponsive?
                    if (current_time - self.status.last_activity_time) > final_timeout:
                        
                        # Update user
                        incomplete_tasks = np.where(self.status.completed_tasks == False)[0].tolist()
                        if (len(incomplete_tasks) == 0):
                            break
                        logger.warning("Unresponsive tasks detected! Tasks: " + ', '.join([str(x) for x in incomplete_tasks]))
                        if silent == False:
                            print("\n\tUnresponsive tasks detected! Tasks: " + ', '.join([str(x) for x in incomplete_tasks]))
                        
                        # Add unresponsive tasks to retry queue
                        for task_id in incomplete_tasks:
                            queue_of_requests_to_retry.put_nowait(self.create_request(
                                    task_id=task_id,
                                    prompt=prompts_deduped[task_id],
                                    user_tokens=input_token_counts[task_id],
                                    max_tokens=max_tokens,
                                    num_max_tokens=num_max_tokens,
                                    temperature=temperature,
                                    num_temps=num_temps,
                                    top_p=top_p,
                                    num_top_ps=num_top_ps,
                                    attempts_left=1,
                            ))
                            
                        self.status.last_activity_time = time.time()
                        self.status.num_tasks_started -= len(incomplete_tasks)
                    
                    # Main loop sleeps briefly so concurrent tasks can run
                    await asyncio.sleep(seconds_to_sleep_each_loop)
                    
                    # If a rate limit error was hit recently, pause to cool down
                    seconds_since_rate_limit_error = time.time() - self.status.time_of_last_rate_limit_error
                    if seconds_since_rate_limit_error < self.seconds_to_pause:
                        remaining_seconds_to_pause = self.seconds_to_pause - seconds_since_rate_limit_error
                        logger.info(
                            "Pausing to cool down until "
                            f"{time.ctime(self.status.time_of_last_rate_limit_error + self.seconds_to_pause)}"
                        )
                        await asyncio.sleep(remaining_seconds_to_pause)
                        
            finally:
                if use_task_group == True:
                    await task_group.__aexit__(None, None, None)
                elif len(active_tasks) > 0:
                    await self.wait_for_tracked_tasks(active_tasks)
       
        # if (self.status.num_tasks_succeeded > self.status.num_tasks_started):
        #     print("\n****")
        #     print("WARNING!! 'final_timeout' setting was too short! Program finished before all tasks had started!")
        #     print("****\n")
        
        # If duplicates, map results back to correct positions
        if has_dupes == True:
            results_deduped = deepcopy(self.results)
            self.results = [results_deduped[item] for item in prompt_index_map]
        
        # Save stats from this run to list
        self.status_list.append(self.status)
        
        # Print time taken
        time_elapsed = self.time_elapsed_string(api_start_time)
        logger.info("Finished API calls" + set_string)
        logger.info(f"\t{time_elapsed}")
        logger.info((
            f"\tTokens In = {self.status.tokens_in} (Cached = {self.status.cached_tokens_in})"
            f"\n\tTokens Out = {self.status.tokens_out} (Reasoning = {self.status.reasoning_tokens_out})"
        ))
        if silent == False:
            print("")
            # print("\nFinished API calls" + set_string)
            print(f"\t{time_elapsed}")
            print((
                f"\tTokens In = {self.status.tokens_in} (Cached = {self.status.cached_tokens_in})"
                f"\n\tTokens Out = {self.status.tokens_out} (Reasoning = {self.status.reasoning_tokens_out})"
            ))
    

    def task_id_generator_function(self) -> str:
        """Generate integers 0, 1, 2, and so on."""
        task_id = 0
        while True:
            yield task_id
            task_id += 1


    async def run_parallel_sets(
        self,
        prompts: list[dict],
        models: list[dict],
        response_format: list[dict] = None,
        api_file_parts: list[dict] = None,
        timeout: int = 500,
        final_timeout: int = 120,
        max_parallel: int = None,
        set_name: str = "",
        silent: bool = False,
    ):
        
        if set_name != "":
            set_name += " #"
        if max_parallel is None:
            max_parallel = self.max_parallel
        
        # Loop over sets
        self.set_results = []
        for i in range(len(models)):
            
            # Determine settings for next set
            m = models[i]
            
            # Update model for next set
            #self.set_client(type=m['type'], team=None, region=m['region'], model=m['model'])
            self.set_client(model_details=m)
            
            # Check max_tokens
            if (m['max_output'] is None or m['max_output'] > self.max_output_tokens):
                m['max_output'] = self.max_output_tokens
            
            # Run next set
            await self.run_parallel(
                prompts=prompts,
                response_format=response_format,
                api_file_parts=api_file_parts,
                max_tokens=m['max_output'],
                temperature=m['temp'],
                top_p=m['top_p'],
                reasoning_effort=m['reasoning'],
                web_search=m['web_search'],
                timeout=timeout,
                final_timeout=final_timeout,
                set_name=set_name + str(i + 1),
                max_parallel=max_parallel,
                silent=silent,
            )
            
            # Save results
            self.set_results.append(self.results)
            
    
    def split_oversized_batch_sets(
        self,
        batch_details: list[dict],
    ) -> tuple[list[dict], list[int]]:
        """
        Split any batch sets whose input .jsonl file would exceed their API's
        maximum batch file size (or maximum request count) into multiple smaller sets.
        Returns the (possibly expanded) batch details list, plus a mapping from each
        expanded set back to the index of the original set it came from, so results
        can be merged back together after the batches run.
        """

        split_details = []
        set_origin = []
        for set_i in range(len(batch_details)):
            details = batch_details[set_i]
            m = details['model_details']
            prompts = details['prompts']

            # Input file limits for this set's API type
            match m['type']:
                case "openai":
                    max_file_bytes = OPENAI_BATCH_MAX_FILE_BYTES
                    max_requests = OPENAI_BATCH_MAX_REQUESTS
                case "vertex" | "meta_vertex":
                    max_file_bytes = VERTEX_BATCH_MAX_FILE_BYTES
                    max_requests = None
                case _:
                    max_file_bytes = None
                    max_requests = None
            if max_file_bytes is None or len(prompts) == 0:
                split_details.append(details)
                set_origin.append(set_i)
                continue
            target_bytes = int(max_file_bytes * BATCH_TARGET_FRACTION)

            # Normalize response formats + api file parts to full length (same rules as run_batch_sets)
            response_formats = details.get('response_formats', None)
            if response_formats is None:
                response_formats = [None] * len(prompts)
            if type(response_formats) is not list:
                response_formats = [response_formats]
            if len(response_formats) < len(prompts):
                response_formats = response_formats + [response_formats[-1]] * (len(prompts) - len(response_formats))
            api_file_parts = details.get('api_file_parts', None)
            if api_file_parts is None:
                api_file_parts = [None] * len(prompts)
            if type(api_file_parts) is not list:
                api_file_parts = [api_file_parts]
            if len(api_file_parts) < len(prompts):
                api_file_parts = api_file_parts + [api_file_parts[-1]] * (len(prompts) - len(api_file_parts))
            details['response_formats'] = response_formats
            details['api_file_parts'] = api_file_parts

            set_name = details['name'] if ('name' in details and details['name'] != "") else f"Set #{set_i+1}"
            set_file_name = details['file_name'] if ('file_name' in details and details['file_name'] != "") else f"set{set_i+1}"

            # Build the requests once (in memory only) to measure the true .jsonl line sizes
            temp_batch = APIBatch.APIBatch(
                id=set_i,
                name=set_name,
                file_name=set_file_name,
                description="size_check",
                api_type=m['type'],
                api_region=m['region'],
                api_model=m['model'],
                google_cloud_project_id="",
                has_formats=any(item is not None for item in response_formats),
                development=self.development,
            )
            temp_batch.build_requests(
                prompts=prompts,
                model=m['model'],
                thinking=self.thinking,
                reasoning=m['reasoning'],
                temp=m['temp'],
                top_p=m['top_p'],
                max_output_tokens=m['max_output'],
                response_formats=response_formats,
                api_file_parts=api_file_parts,
                silent=True,
            )
            # Requests are keyed by deduped prompt index via custom_id
            # None prompts produce no request at all, so their size is 0
            num_deduped = len(temp_batch.dedupe_original_index)
            deduped_sizes = [0] * num_deduped
            for req in temp_batch.requests:
                deduped_sizes[int(req['custom_id'])] = len(json.dumps(req).encode('utf-8')) + 1
            # Size of each original prompt's request (duplicates share their deduped request's size)
            prompt_sizes = [deduped_sizes[temp_batch.dupe_index_map[i]] for i in range(len(prompts))]
            total_bytes = sum(deduped_sizes)
            num_requests = len(temp_batch.requests)
            temp_batch.close()
            del temp_batch

            # If under the limits, keep the set as-is
            if total_bytes <= target_bytes and (max_requests is None or num_requests <= max_requests):
                split_details.append(details)
                set_origin.append(set_i)
                continue

            # Else, greedily pack contiguous prompts into parts that stay under the limits
            part_ranges = []
            part_start = 0
            part_bytes = 0
            for i in range(len(prompts)):
                if i > part_start and (part_bytes + prompt_sizes[i] > target_bytes or (max_requests is not None and (i - part_start) >= max_requests)):
                    part_ranges.append((part_start, i))
                    part_start = i
                    part_bytes = 0
                part_bytes += prompt_sizes[i]
                if prompt_sizes[i] > target_bytes:
                    logger.warning(f"Batch '{set_name}': A single request (~{prompt_sizes[i]/1048576:.1f}MB)"
                                   f" exceeds the {m['type']} batch input file limit and cannot be split further.")
            part_ranges.append((part_start, len(prompts)))

            num_parts = len(part_ranges)
            msg = (f"Batch '{set_name}': input file would be ~{total_bytes/1048576:.1f}MB ({num_requests} requests),"
                   f" over the {m['type']} batch input file limit - splitting into {num_parts} parts.")
            logger.info(msg)
            print(f"\n\t{msg}")

            for p in range(num_parts):
                a, b = part_ranges[p]
                part = dict(details)
                part['name'] = f"{set_name} (Part {p+1} of {num_parts})"
                part['file_name'] = f"{set_file_name}_part{p+1}"
                part['prompts'] = prompts[a:b]
                part['response_formats'] = response_formats[a:b]
                part['api_file_parts'] = api_file_parts[a:b]
                split_details.append(part)
                set_origin.append(set_i)

        return split_details, set_origin


    async def run_batch_sets(
        self,
        batch_details: list[dict],
        batch_name: str,
        batch_path: pathlib.Path,
        update_delay: int = 60,
    ):
        batch_sets_start_time = time.time()
        
        # Google Cloud Project ID
        google_cloud_project_id = "psb-vertex-testing"
        
        # Check arguments
        if type(batch_details) is not list:
            batch_details = [batch_details]
        num_batches = len(batch_details)
        for set_i in range(num_batches):
            if 'prompts' not in batch_details[set_i]:
                print("\n***ERROR*** run_batch_sets() - Missing 'prompts' in batch details list!!\n")
                sys.exit()
            if 'model_details' not in batch_details[set_i]:
                print("\n***ERROR*** run_batch_sets() - Missing 'model_details' in batch details list!!\n")
                sys.exit()

        # Split any sets whose batch input file would exceed the API's size limits
        num_original_sets = num_batches
        batch_details, set_origin = self.split_oversized_batch_sets(batch_details=batch_details)
        num_batches = len(batch_details)
        
        print(f"\n\tInitializing batches (n={num_batches})")
        
        # Loop over sets to build batch request objects
        self.set_results = [None] * num_batches
        batch_list = [None] * num_batches
        for set_i in range(num_batches):
            
            # Determine name for this set
            if 'name' in batch_details[set_i] and batch_details[set_i]['name'] != "":
                set_name = batch_details[set_i]['name']
            else:
                set_name = f"Set #{set_i+1}"
            if 'file_name' in batch_details[set_i] and batch_details[set_i]['file_name'] != "":
                set_file_name = batch_details[set_i]['file_name']
            else:
                set_file_name = f"set{set_i+1}"
            
            # Determine prompts and response formats for this set
            has_formats = False
            prompts = batch_details[set_i]['prompts']
            if 'response_formats' in batch_details[set_i]:
                response_formats = batch_details[set_i]['response_formats']
            else:
                response_formats = [None] * len(prompts)
            if type(response_formats) is not list:
                response_formats = [response_formats]
            has_formats = any(item is not None for item in response_formats)
            if len(response_formats) < len(prompts): # expand formats to same length as prompts
                response_formats = response_formats + [response_formats[-1]] * (len(prompts) - len(response_formats))
            batch_details[set_i]['response_formats'] = response_formats
            
            # API 'parts'
            if 'api_file_parts' in batch_details[set_i]:
                api_file_parts = batch_details[set_i]['api_file_parts']
            else:
                api_file_parts = [None] * len(prompts)
            if type(api_file_parts) is not list:
                api_file_parts = [api_file_parts]
            if len(api_file_parts) < len(prompts): # expand parts to same length as prompts
                api_file_parts = api_file_parts + [api_file_parts[-1]] * (len(prompts) - len(api_file_parts))
            batch_details[set_i]['api_file_parts'] = api_file_parts
            
            # Model details
            m = batch_details[set_i]['model_details']
            
            # Initialize batch object
            self.set_client(model_details=m)
            new_batch = APIBatch.APIBatch(
                id=set_i,
                name=set_name,
                file_name=set_file_name,
                description=batch_name,
                api_type=m['type'],
                api_region=m['region'],
                api_model=m['model'],
                google_cloud_project_id=google_cloud_project_id,
                has_formats=has_formats,
                development=self.development,
            )
            
            # Build requests
            new_batch.build_requests(
                prompts=prompts,
                model=m['model'],
                thinking=self.thinking,
                reasoning=m['reasoning'],
                temp=m['temp'],
                top_p=m['top_p'],
                max_output_tokens=m['max_output'],
                response_formats=response_formats,
                api_file_parts=api_file_parts,
            )
            
            # Only upload and start if requests exists
            if len(new_batch.requests) > 0:
                
                # Save requests to JSON file
                new_batch.save_requests_to_file(directory=batch_path)
                
                # Upload requests to API
                new_batch.upload_requests(directory=batch_path)
                
                # Start batch job
                new_batch.start()
            
            # If zero requests, then batch is already finished
            else:
                new_batch.finished = True
            
            # Add object to list
            batch_list[set_i] = new_batch
        
        print("\n\tRunning batches:")
        
        # Wait until all batches are finished
        colorama.init()
        finished = False
        processed = False
        while finished == False and processed == False:
            
            # Update status of all batches
            finished = True
            processed = True
            for batch in batch_list:
                if batch.finished == False:
                    batch.update_status()
                    if batch.finished == False:
                        finished = False
                    
                    # If finished, download and process results
                    else:
                        batch.download_results(directory=batch_path)
                        batch.process_results(directory=batch_path)
                    
                # Processed?
                if batch.processed == False:
                    processed = False
            
            # Print progress
            for batch in batch_list:
                # \r -> col-0, \x1b[2K -> clear the line, then status message
                print(f"\r\x1b[2K\t\t{batch.status_string}".ljust(15))
            sys.stdout.flush()
            sys.stdout.write(f"\x1b[{num_batches}A")

            # Wait
            time.sleep(update_delay)
        
        # When finished, move the console cursor below progress lines
        sys.stdout.write(f"\x1b[{num_batches}B")
        sys.stdout.flush()
        
        # Save status tracker results
        for set_i in range(num_batches):
            self.status_list.append(batch_list[set_i].status_tracker)
        
        # Delete files on server
        for batch in batch_list:
            batch.delete_server_files()
           
        # Print time taken
        time_elapsed = self.time_elapsed_string(batch_sets_start_time)
        print("")
        print(f"\t{time_elapsed}")
        
        # If any errors, retry by rerunning individually
        for set_i in range(num_batches):
            batch = batch_list[set_i]
            if len(batch.retries) > 0:
                print(f"\n\tRunning retries for {batch.name}...")
                m = batch_details[set_i]['model_details']
                self.set_client(model_details=m)
                # Retry IDs are custom_ids assigned AFTER dedupe - map them back to original prompt indexes
                retry_orig = [batch.dedupe_original_index[retry_i] for retry_i in batch.retries]
                await self.run_parallel(
                    prompts=[batch_details[set_i]['prompts'][orig_i] for orig_i in retry_orig],
                    response_format=[batch_details[set_i]['response_formats'][orig_i] for orig_i in retry_orig],
                    api_file_parts=[batch_details[set_i]['api_file_parts'][orig_i] for orig_i in retry_orig],
                    max_tokens=m['max_output'],
                    temperature=m['temp'],
                    top_p=m['top_p'],
                    reasoning_effort=m['reasoning'],
                    set_name=f"{batch.name} (Retries)",
                    silent=True,
                )
                with open(pathlib.Path(batch_path, f"{batch.name}_retries_raw_results.txt"), "w", encoding="utf-8") as f:
                    for retry_i in range(len(self.results)):
                        batch.results[batch.retries[retry_i]] = self.results[retry_i]
                        f.write(f"\n--result ID#{batch.retries[retry_i]} :\n")
                        f.write(f"{self.results[retry_i]}\n")
                        
            # Re-duplicate results, if necessary
            batch.duplicate_results()
        
        # Add final results to APITracker, merging any split parts back into their original sets
        # (parts are contiguous slices in order, so concatenating restores the original prompt order)
        self.set_results = [[] for item in range(num_original_sets)]
        for set_i in range(num_batches):
            self.set_results[set_origin[set_i]].extend(batch_list[set_i].results)
        

def load_data(
        directory: str,
        input_file: str,
        input_sheet: str,
        metadata_file: str,
        input_id_col: str,
        extra_cols: list[str],
        ID_COL: str,
) -> tuple[pd.DataFrame, dict]:
    """Reads raw responses from input file and loads into a pandas dataframe and a dictionary containing metadata"""
    
    # Check if input file exists
    input_path = pathlib.Path(directory, input_file)
    if not input_path.exists():
        print(f"\n***ERROR*** load_data() - Cannot locate file at {input_path}\n")
        sys.exit()
    
    # Call appropriate function based on input file extension
    match input_path.suffix:
        
        # Read in SPSS data
        case ".sav":            
            data_df, sav_meta = read_sav(input_path)
            metadata = {}
            for i in range(sav_meta.number_columns):
                sav_name = sav_meta.column_names[i]
                metadata[sav_name] = {
                    "name": sav_meta.column_names[i],
                    "label": sav_meta.column_labels[i],
                    "class": sav_meta.readstat_variable_types[sav_name],
                }
                if sav_name in sav_meta.variable_value_labels:
                    metadata[sav_name]['elements'] = {
                        "values": [k for k, v in sav_meta.variable_value_labels[sav_name].items()],
                        "labels": [v for k, v in sav_meta.variable_value_labels[sav_name].items()],
                    }
        
        # Read in Excel data
        case ".xlsx":
            data_df = pd.read_excel(input_path, sheet_name=input_sheet)
            metadata = None
        
        # Read in CSV data
        case ".csv":
            data_df = pd.read_csv(input_path, low_memory=False)
            metadata = None
        
        # Read in delimited data
        case _:
            metadata_path = pathlib.Path(directory, metadata_file)
            if not metadata_path.exists():
                err: str = f"\n***ERROR*** load_data() - Cannot locate file at {metadata_path}\n"
                logger.error(err)
                raise FileNotFoundError(err)
            data_df = pd.read_table(input_path, na_values=[""," "], keep_default_na=False, low_memory=False)
            with open(metadata_path, 'r', encoding='utf-8') as json_file:
                metadata = json.load(json_file)

    # Check if ID column exists
    data_cols = [col for col in data_df.columns]
    data_cols_lower = [col.lower() for col in data_cols]
    if input_id_col.lower() not in data_cols_lower:
        err: str = f"\n***ERROR*** load_data() - input_id_col '{input_id_col}' does not exist in {input_file}\n"
        logger.error(err)
        raise ValueError(err)
    input_id_column = data_cols[data_cols_lower.index(input_id_col.lower())]
    
    # Check that ID column has all unique values
    if not data_df[input_id_column].is_unique:
        dupes: list[str] = data_df.loc[data_df[input_id_column].duplicated(keep=False), input_id_column].drop_duplicates()
        err: str = f"\n***ERROR*** load_data() - Duplicate IDs found in input file: {dupes.tolist()}"
        logger.error(err)
        raise ValueError(err)
    
    # Extra columns?
    if len(extra_cols) > 0:
        for i in range(len(extra_cols)):
            if extra_cols[i].lower() not in data_cols_lower:
                err: str = f"\n***ERROR*** load_data() - extra_col '{extra_cols[i]}' does not exist in {input_file}\n"
                logger.error(err)
                raise ValueError(err)
            extra_cols[i] = data_cols[data_cols_lower.index(extra_cols[i].lower())]
            
            # # If needed, convert extra columns to categorical values
            # if metadata is not None and metadata[extra_col]['class'] == "factor":
            #     replace_dict = {k: v for k, v in zip(metadata[extra_col]['elements']['values'],
            #                                          metadata[extra_col]['elements']['labels'])}
            #     data_df[extra_col] = data_df[extra_col].replace(replace_dict)
    
    # Rename ID column
    if input_id_column != ID_COL:
        data_df.rename(columns={input_id_column: ID_COL}, inplace = True)
    
    # Remove any invalid rows, which is any row that does not have an ID value
    data_df.dropna(subset=[ID_COL], inplace=True)
    data_df.index = pd.RangeIndex(0, len(data_df.index))
    
    return data_df, metadata


def add_demo_col(
        data_df: pd.DataFrame,
        metadata: dict,
        DEMO_COL: str,
        demo_list: list[dict],
) -> pd.DataFrame:
    """ """
    
    out_df = data_df.copy()
    data_cols = [col for col in out_df.columns]
    data_cols_lower = [col.lower() for col in data_cols]
    
    # Make sure all columns exists and are in correct case
    for i in range(len(demo_list)):
        if 'name' not in demo_list[i]:
            print("\n***ERROR*** add_demo_col() - 'name' attribute missing from item in demo list!\n")
            sys.exit()
        elif demo_list[i]['name'].lower() not in data_cols_lower:
            print(f"\n***ERROR*** add_demo_col() - Column '{demo_list[i]['name']}' does not exist in dataframe!\n")
            sys.exit()
        else:
            demo_list[i]['name'] = data_cols[data_cols_lower.index(demo_list[i]['name'].lower())]
            if 'label' not in demo_list[i]:
                demo_list[i]['label'] = metadata[demo_list[i]['name']]['label']
    
    # Build demographic strings
    num_rows = len(out_df.index)
    full_demo = pd.Series(["" for x in range(len(out_df.index))])
    for demo in demo_list:
        demo_data = out_df[demo['name']].astype(str)
        if metadata is not None and "elements" in metadata[demo['name']]:
            demo_replace_dict = {str(k): v for k, v in zip(metadata[demo['name']]['elements']['values'],
                                                           metadata[demo['name']]['elements']['labels'])}
            for row in range(num_rows):
                if demo_data[row] and pd.isnull(demo_data[row]) == False and demo_data[row] != "nan":
                    full_demo[row] += "\n" + demo['label'] + ": " + demo_replace_dict[demo_data[row]]
        else:
            for row in range(num_rows):
                if demo_data[row] and pd.isnull(demo_data[row]) == False and demo_data[row] != "nan":
                    full_demo[row] += "\n" + demo['label'] + ": " + demo_data[row]
                    
    out_df[DEMO_COL] = full_demo.str.strip()
    
    # Return dataframe
    return out_df


def add_interview_col(
        data_df: pd.DataFrame,
        metadata: dict,
        INTERVIEW_COL: str,
        interview_list: list[dict],
) -> pd.DataFrame:
    """ """
    
    # Disallow interview column if metadata is missing
    if metadata is None:
        print("\n***ERROR*** add_interview_col() - Cannot build interviews without metadata!\n")
        sys.exit()
    
    out_df = data_df.copy()
    data_cols = [col for col in data_df.columns]
    data_cols_lower = [col.lower() for col in data_cols]
    
    # Make sure all columns exists and are in correct case
    for i in range(len(interview_list)):
        if 'name' not in interview_list[i]:
            print("\n***ERROR*** add_interview_col() - 'name' attribute missing from item in interview list!\n")
            sys.exit()
        elif interview_list[i]['name'].lower() not in data_cols_lower:
            print(f"\n***ERROR*** add_interview_col() - Column '{interview_list[i]['name']}' does not exist in dataframe!\n")
            sys.exit()
        else:
            interview_list[i]['name'] = data_cols[data_cols_lower.index(interview_list[i]['name'].lower())]
            if 'follow-ups' in interview_list[i]:
                for j in range(len(interview_list[i]['follow-ups'])):
                    if 'q' not in interview_list[i]['follow-ups'][j] or 'a' not in interview_list[i]['follow-ups'][j]:
                        print("\n***ERROR*** add_interview_col() - 'q' and/or 'a' attributes missing from follow-up item for {interview_list[i]['name']}!\n")
                        sys.exit()
                    
                    if interview_list[i]['follow-ups'][j]['q'].lower() not in data_cols_lower:
                        print(f"\n***ERROR*** add_interview_col() - Follow-up column '{interview_list[i]['follow-ups'][j]['q']}' does not exist in dataframe!\n")
                        sys.exit()
                    else:
                        interview_list[i]['follow-ups'][j]['q'] = data_cols[data_cols_lower.index(interview_list[i]['follow-ups'][j]['q'].lower())]
                    
                    if interview_list[i]['follow-ups'][j]['a'].lower() not in data_cols_lower:
                        print(f"\n***ERROR*** add_interview_col() - Follow-up column '{interview_list[i]['follow-ups'][j]['a']}' does not exist in dataframe!\n")
                        sys.exit()
                    else:
                        interview_list[i]['follow-ups'][j]['a'] = data_cols[data_cols_lower.index(interview_list[i]['follow-ups'][j]['a'].lower())]
    
    # Build full interview strings
    num_rows = len(out_df.index)
    full_interview = pd.Series(["" for x in range(num_rows)])
    for info in interview_list:
        info_label = ""
        if metadata is not None: info_label = metadata[info['name']]['label']
        for row in range(num_rows):
            if out_df[info['name']][row] and pd.isnull(out_df[info['name']][row]) == False:
                full_interview[row] += "\nINTERVIEWER: " + info_label + "\nRESPONDENT: " + str(out_df[info['name']][row])
        if ("follow-ups" in info):
            for fu in info['follow-ups']:
                for row in range(num_rows):
                    if out_df[fu['a']][row] and pd.isnull(out_df[fu['a']][row]) == False:
                        full_interview[row] += "\nINTERVIEWER: " + str(out_df[fu['q']][row])
                        full_interview[row] += "\nRESPONDENT: " + str(out_df[fu['a']][row])
    out_df[INTERVIEW_COL] = full_interview.str.strip()
    
    # Return dataframe
    return out_df
    

def replace_interview_strings(
        data_df: pd.DataFrame,
        metadata: dict,
        input_oe_col: str,
        INTERVIEW_COL: str,
        replace_list: list[dict],
        col: str = None,
) -> pd.DataFrame:
    
    out_df = data_df.copy()
    data_cols = [item for item in data_df.columns]
    data_cols_lower = [item.lower() for item in data_cols]
    
    # Check 'col' argument
    if col is None:
        if INTERVIEW_COL in out_df:
            col = INTERVIEW_COL
        elif input_oe_col in out_df:
            col = input_oe_col
        else:
            print(f"\n***ERROR*** replace_interview_strings() - Neither interview column nor input_oe_col '{input_oe_col}' found!\n")
            sys.exit()
    else:
        if col.lower() not in data_cols_lower:
            print(f"\n***ERROR*** replace_interview_strings() - Column '{col}' does not exist in dataframe!\n")
            sys.exit()
        col = data_cols[data_cols_lower.index(col.lower())]
    
    # Loop over replacements
    for item in replace_list:
        
        # Make sure correct attributes are present
        if 'replace' not in item:
            print("\n***ERROR*** replace_interview_strings() - 'replace' attribute missing from item in replace list!\n")
            sys.exit()
        elif 'column' not in item and 'value' not in item:
            print(f"\n***ERROR*** replace_interview_strings() - '{item['replace']}' item in the replace list needs either a 'column' or 'value' attribute!\n")
            sys.exit()
            
        # If replace by 'value'
        if 'value' in item:
            out_df[col] = out_df[col].str.replace(item['replace'], item['value'], case=False)
        
        # If replace by 'column'
        elif 'column' in item:
            if item['column'].lower() not in data_cols_lower:
                print(f"\n***ERROR*** replace_interview_strings() - Column '{item['column']}' does not exist in dataframe!\n")
                sys.exit()
            replace_col = data_cols[data_cols_lower.index(item['column'].lower())]
            if metadata is not None and 'elements' in metadata[replace_col]:
                replace_dict = {str(k): v for k, v in zip(metadata[replace_col]['elements']['values'], metadata[replace_col]['elements']['labels'])}
                replace_dict['nan'] = "NA"
                out_df[col] = [x.replace(item['replace'], replace_dict[y]) for x, y in out_df[[col,replace_col]].to_numpy().astype(str)]
            else:
                out_df[col] = [x.replace(item['replace'], y) for x, y in out_df[[col,replace_col]].to_numpy().astype(str)]
    
    # Return dataframe
    return out_df


def load_responses(
        data_df: pd.DataFrame,
        metadata: dict,
        input_oe_col: str,
        ID_COL: str,
        RESP_COL: str,
        DEMO_COL: str,
        INTERVIEW_COL: str,
        TOKENS_COL: str,
        TOKENS_MODEL: str,
        extra_cols: list[str],
        pre_interview: bool,
        input_title_col: str = None,
        TITLE_COL: str = None,
) -> pd.DataFrame:
    """ """
    
    temp_df = deepcopy(data_df)
    new_cols_dict: dict = {}
    
    # If no interview, save response columns at the end with default names
    if pre_interview or INTERVIEW_COL not in temp_df:
        # Title column?
        if input_title_col:
            if not TITLE_COL:
                print(f"\n***ERROR*** load_responses() - input_title_col '{input_title_col}' given, but missing TITLE_COL value!\n")
                sys.exit()
            if input_title_col not in temp_df:
                print(f"\n***ERROR*** load_responses() - input_title_col '{input_title_col}' does not exist in dataframe!\n")
                sys.exit()
            if input_title_col != TITLE_COL:
                new_cols_dict[TITLE_COL] = temp_df[input_title_col].fillna("").astype(str)
            
        # Input column
        if input_oe_col not in temp_df:
            print(f"\n***ERROR*** load_responses() - input_oe_col '{input_oe_col}' does not exist in dataframe!\n")
            sys.exit()
        if input_oe_col != RESP_COL:
            new_cols_dict[RESP_COL] = temp_df[input_oe_col]
    
    # Else if interview was created, then use that instead
    else:
        if input_title_col:
            new_cols_dict[TITLE_COL] = ""
        new_cols_dict[RESP_COL] = temp_df[INTERVIEW_COL]
    
    # Append new TITLE/RESP columns
    temp_df = pd.concat( [ temp_df, pd.DataFrame(new_cols_dict) ], axis=1 )
    
    # Warn if interview constructed but not used
    if pre_interview and INTERVIEW_COL in temp_df:
        print("\n***ERROR***\nload_responses() - Interviews were auto-constructed using 'add_interview_col()' but are not being used because 'input_oe_is_interview = True'!\n***ERROR***\n")
        sys.exit()
    
    # Warn if interview constructed but oe column was supplied
    if input_oe_col and INTERVIEW_COL in temp_df:
        print(f"\n***ERROR***\nload_responses() - Interviews were auto-constructed using 'add_interview_col()' but input_oe_col was set to '{input_oe_col}'!\n***ERROR***\n")
        sys.exit()
    
    # Make sure response column is of type string for every item
    if input_title_col:
        temp_df[TITLE_COL] = temp_df[TITLE_COL].astype(str)
    temp_df[RESP_COL] = temp_df[RESP_COL].astype(str)
    
    # Count number of tokens in the response column and save results in a new column
    try:
        token_encoding = tiktoken.encoding_for_model(TOKENS_MODEL)
    except:
        token_encoding = tiktoken.get_encoding("o200k_base") # Encoding used by gpt-4o, gpt-5, etc
    temp_df = pd.concat([
        temp_df,
        pd.DataFrame({ TOKENS_COL: temp_df[RESP_COL].apply(lambda x: 0 if pd.isna(x) else len(token_encoding.encode(x))) })
    ], axis=1)
    
    # Keep only the needed columns
    col_names = [ID_COL] + extra_cols + [DEMO_COL, TITLE_COL, RESP_COL, TOKENS_COL]
    resp_df = temp_df.filter(col_names)
    
    # If needed, convert extra columns to categorical values
    if len(extra_cols) > 0:
        for extra_col in extra_cols:
            if metadata is not None and "elements" in metadata[extra_col]:
                replace_dict = {k: v for k, v in zip(metadata[extra_col]['elements']['values'],
                                                     metadata[extra_col]['elements']['labels'])}
                resp_df[extra_col] = resp_df[extra_col].replace(replace_dict)
    
    # Place quote before responses that start with html, so that they are not treated as links when exporting
    if input_title_col:
        resp_df[TITLE_COL] = resp_df[TITLE_COL].mask(
            resp_df[TITLE_COL].str.startswith(("http://", "https://")),
            "'" + resp_df[TITLE_COL]
        )
    resp_df[RESP_COL] = resp_df[RESP_COL].mask(
        resp_df[RESP_COL].str.startswith(("http://", "https://")),
        "'" + resp_df[RESP_COL]
    )
    
    # Return dataframe
    return resp_df
