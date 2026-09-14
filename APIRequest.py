# -*- coding: utf-8 -*-
"""
@author: mmorrell
Version:  2.3.0
Version Creation: 07/06/2026
"""


# imports
import asyncio
from dataclasses import (
    dataclass,
    field,
)
from google import genai
import json
import logging
import numpy as np
from openai import (
    RateLimitError as openai_RateLimitError,
    NotFoundError as openai_NotFoundError,
    BadRequestError as openai_BadRequestError,
)
from pydantic import BaseModel
from sys import exit
#import tiktoken
import time
from typing import Type

# logging
logger = logging.getLogger(__name__)

@dataclass
class APIStatus:
    """Stores API usage statistics"""
    
    model: str = ""
    set_name: str = ""
    
    num_tasks: int = 0
    num_tasks_started: int = 0
    num_tasks_in_progress: int = 0
    num_tasks_succeeded: int = 0
    num_tasks_failed: int = 0
    
    num_rate_limit_errors: int = 0
    num_api_errors: int = 0
    num_other_errors: int = 0
    time_of_last_rate_limit_error: float = 0.0
    
    max_tpm: int = 0
    max_rpm: int = 0
    available_tpm: int = 0
    available_rpm: int = 0
    calculated_tpm: float = 0.0
    calculated_rpm: float = 0.0
    
    tokens_in: int = 0
    cached_tokens_in: int = 0
    tokens_out: int = 0
    reasoning_tokens_out: int = 0
    last_request_tokens: int = 0
    
    total_cost: float = 0.0
    
    completed_tasks: np.ndarray = field(default_factory=lambda: np.array([]))
    last_activity_time: float = time.time()
    
    def print_progress(
            self,
            has_dupes: bool = False,
    ):
        if has_dupes == False:
            dupe_string = ""
        else:
            dupe_string = "(deduped)"
        print(
            "\r\t\t"            
            f"{self.num_tasks_succeeded} of {self.num_tasks}{dupe_string} completed,"
            f" {self.num_tasks_in_progress} in progress,"
            f" TPM:{int(self.calculated_tpm)},"
            f" RPM:{int(self.calculated_rpm)}"
            "          ",
            end="\r",flush=True
        )
    
    
    def est_openai_cost(
            self,
            prompt_cost: float,
            output_cost: float,
            prompt_cost_lg: float,
            output_cost_lg: float,
            in_t: int,
            cin_t: int,
            out_t: int,
    ) -> float:
        
        if in_t <= 272_000:
            cost_est = (in_t / 1000000 * prompt_cost)
            cost_est += (out_t / 1000000 * output_cost)
        else:
            cost_est = (in_t / 1000000 * prompt_cost_lg)
            cost_est += (out_t / 1000000 * output_cost_lg)
        
        return cost_est
    
    
    def est_openai_cost_w_cached(
            self,
            prompt_cost: float,
            cached_cost: float,
            output_cost: float,
            prompt_cost_lg: float,
            cached_cost_lg: float,
            output_cost_lg: float,
            in_t: int,
            cin_t: int,
            out_t: int,
    ) -> float:
        
        if in_t <= 272_000:
            cost_est = ((in_t - cin_t) / 1000000 * prompt_cost)
            cost_est += (cin_t / 1000000 * cached_cost)
            cost_est += (out_t / 1000000 * output_cost)
        else:
            cost_est = ((in_t - cin_t) / 1000000 * prompt_cost_lg)
            cost_est += (cin_t / 1000000 * cached_cost_lg)
            cost_est += (out_t / 1000000 * output_cost_lg)
        
        return cost_est
    
    
    def est_gemini_cost(
            self,
            prompt_cost: float,
            output_cost: float,
            prompt_cost_lg: float,
            output_cost_lg: float,
            in_t: int,
            cin_t: int,
            out_t: int,
    ) -> float:
        
        if in_t <= 200_000:
            cost_est = ((in_t - cin_t) / 1000000 * prompt_cost)
            cost_est += (cin_t / 1000000 * (prompt_cost * 0.25))
            cost_est += (out_t / 1000000 * output_cost)
        else:
            cost_est = ((in_t - cin_t) / 1000000 * prompt_cost_lg)
            cost_est += (cin_t / 1000000 * (prompt_cost_lg * 0.25))
            cost_est += (out_t / 1000000 * output_cost_lg)
        
        return cost_est
    
    
    def estimate_costs(
            self,
            model_name: str,
            in_t: int,
            cin_t: int,
            out_t: int,
    ) -> float:
        """Estimates the total cost of tokens in+out, based on current model"""
        
        cost_est = 0
        match model_name:
            
            ### gpt-5.6 ###
            case "gpt-5.6-sol":
                cost_est = self.est_openai_cost_w_cached(prompt_cost=5.00, cached_cost=0.50, output_cost=30.00,
                                                         prompt_cost_lg=10.00, cached_cost_lg=1.00, output_cost_lg=45.00,
                                                         in_t=in_t, cin_t=cin_t, out_t=out_t)
            case "gpt-5.6-terra":
                cost_est = self.est_openai_cost_w_cached(prompt_cost=2.50, cached_cost=0.25, output_cost=15.00,
                                                         prompt_cost_lg=5.00, cached_cost_lg=0.50, output_cost_lg=22.50,
                                                         in_t=in_t, cin_t=cin_t, out_t=out_t)
            case "gpt-5.6-luna":
                cost_est = self.est_openai_cost_w_cached(prompt_cost=1.00, cached_cost=0.10, output_cost=6.00,
                                                         prompt_cost_lg=2.00, cached_cost_lg=0.20, output_cost_lg=9.00,
                                                         in_t=in_t, cin_t=cin_t, out_t=out_t)
            
            ### gpt-5.5 ###
            case "gpt-5.5-pro" | "gpt-5.5-pro-2026-04-23":
                cost_est = self.est_openai_cost(prompt_cost=30.00, output_cost=180.00,
                                                prompt_cost_lg=60.00, output_cost_lg=270.00,
                                                in_t=in_t, cin_t=cin_t, out_t=out_t)
            case "gpt-5.5" | "gpt-5.5-2026-04-23":
                cost_est = self.est_openai_cost_w_cached(prompt_cost=5.00, cached_cost=0.50, output_cost=30.00,
                                                         prompt_cost_lg=10.00, cached_cost_lg=1.00, output_cost_lg=45.00,
                                                         in_t=in_t, cin_t=cin_t, out_t=out_t)
            
            ### gpt-5.4 ###
            case "gpt-5.4-pro" | "gpt-5.4-pro-2026-03-05":
                cost_est = self.est_openai_cost(prompt_cost=30.00, output_cost=180.00,
                                                prompt_cost_lg=60.00, output_cost_lg=270.00,
                                                in_t=in_t, cin_t=cin_t, out_t=out_t)
            case "gpt-5.4" | "gpt-5.4-2026-03-05":
                cost_est = self.est_openai_cost_w_cached(prompt_cost=2.50, cached_cost=0.25, output_cost=15.00,
                                                         prompt_cost_lg=5.00, cached_cost_lg=0.50, output_cost_lg=22.50,
                                                         in_t=in_t, cin_t=cin_t, out_t=out_t)
            case "gpt-5.4-mini" | "gpt-5.4-mini-2026-03-17":
                cost_est = self.est_openai_cost_w_cached(prompt_cost=0.75, cached_cost=0.0075, output_cost=4.50,
                                                         prompt_cost_lg=0.75, cached_cost_lg=0.0075, output_cost_lg=4.50,
                                                         in_t=in_t, cin_t=cin_t, out_t=out_t)
            case "gpt-5.4-nano" | "gpt-5.4-nano-2026-03-17":
                cost_est = self.est_openai_cost_w_cached(prompt_cost=0.20, cached_cost=0.02, output_cost=1.25,
                                                         prompt_cost_lg=0.20, cached_cost_lg=0.02, output_cost_lg=1.25,
                                                         in_t=in_t, cin_t=cin_t, out_t=out_t)
            
            ### gpt-5.2 ###
            case "gpt-5.2-pro" | "gpt-5.2-pro-2025-12-11":
                cost_est = self.est_openai_cost(prompt_cost=21.00, output_cost=168.00,
                                                prompt_cost_lg=21.00, output_cost_lg=168.00,
                                                in_t=in_t, cin_t=cin_t, out_t=out_t)
            case "gpt-5.2" | "gpt-5.2-2025-12-11":
                cost_est = self.est_openai_cost_w_cached(prompt_cost=1.75, cached_cost=0.175, output_cost=14.00,
                                                         prompt_cost_lg=1.75, cached_cost_lg=0.175, output_cost_lg=14.00,
                                                         in_t=in_t, cin_t=cin_t, out_t=out_t)
            
            ### gpt-5.1 ###
            case "gpt-5.1" | "gpt-5.1-2025-11-13":
                cost_est = self.est_openai_cost_w_cached(prompt_cost=1.25, cached_cost=0.125, output_cost=10.00,
                                                         prompt_cost_lg=1.25, cached_cost_lg=0.125, output_cost_lg=10.00,
                                                         in_t=in_t, cin_t=cin_t, out_t=out_t)
            
            ##### gpt-5 AND o3 MODELS WILL BE DEPRECATED ON DECEMBER 10TH, 2026 ######
            ### gpt-5 ###
            case "gpt-5-pro" | "gpt-5-pro-2025-10-06":
                cost_est = self.est_openai_cost(prompt_cost=15.00, output_cost=120.00,
                                                prompt_cost_lg=15.00, output_cost_lg=120.00,
                                                in_t=in_t, cin_t=cin_t, out_t=out_t)
            case "gpt-5" | "gpt-5-2025-08-07":
                cost_est = self.est_openai_cost_w_cached(prompt_cost=1.25, cached_cost=0.125, output_cost=10.00,
                                                         prompt_cost_lg=1.25, cached_cost_lg=0.125, output_cost_lg=10.00,
                                                         in_t=in_t, cin_t=cin_t, out_t=out_t)
            case "gpt-5-mini" | "gpt-5-mini-2025-08-07":
                cost_est = self.est_openai_cost_w_cached(prompt_cost=0.25, cached_cost=0.0025, output_cost=2.00,
                                                         prompt_cost_lg=0.25, cached_cost_lg=0.0025, output_cost_lg=2.00,
                                                         in_t=in_t, cin_t=cin_t, out_t=out_t)
            case "gpt-5-nano" | "gpt-5-nano-2025-08-07":
                cost_est = self.est_openai_cost_w_cached(prompt_cost=0.05, cached_cost=0.005, output_cost=0.40,
                                                         prompt_cost_lg=0.05, cached_cost_lg=0.005, output_cost_lg=0.40,
                                                         in_t=in_t, cin_t=cin_t, out_t=out_t)
                
            ##### gpt-5 AND o3 MODELS WILL BE DEPRECATED ON DECEMBER 10TH, 2026 ######
            ### o3 ###
            case "o3" | "o3-2025-04-16":
                cost_est = self.est_openai_cost_w_cached(prompt_cost=2.00, cached_cost=0.50, output_cost=8.00,
                                                         prompt_cost_lg=2.00, cached_cost_lg=0.50, output_cost_lg=8.00,
                                                         in_t=in_t, cin_t=cin_t, out_t=out_t)
            case "o3-pro" | "o3-pro-2025-06-10":
                cost_est = self.est_openai_cost(prompt_cost=20.00, output_cost=80.00,
                                                prompt_cost_lg=20.00, output_cost_lg=80.00,
                                                in_t=in_t, cin_t=cin_t, out_t=out_t)
            
            ### gpt-4.1 ###
            case "gpt-4.1" | "gpt-4.1-2025-04-14":
                cost_est = self.est_openai_cost_w_cached(prompt_cost=2.00, cached_cost=0.50, output_cost=8.00,
                                                         prompt_cost_lg=2.00, cached_cost_lg=0.50, output_cost_lg=8.00,
                                                         in_t=in_t, cin_t=cin_t, out_t=out_t)
            case "gpt-4.1-mini" | "gpt-4.1-mini-2025-04-14":
                cost_est = self.est_openai_cost_w_cached(prompt_cost=0.40, cached_cost=0.10, output_cost=1.60,
                                                         prompt_cost_lg=0.40, cached_cost_lg=0.10, output_cost_lg=1.60,
                                                         in_t=in_t, cin_t=cin_t, out_t=out_t)
                
            ### gpt-4o ###
            case "gpt-4o" | "gpt-4o-s" | "gpt-4o-2024-08-06" | "gpt-4o-2024-11-20":
                cost_est = self.est_openai_cost_w_cached(prompt_cost=2.50, cached_cost=1.25, output_cost=10.00,
                                                         prompt_cost_lg=2.50, cached_cost_lg=1.25, output_cost_lg=10.00,
                                                         in_t=in_t, cin_t=cin_t, out_t=out_t)
            case "gpt-4o-mini" | "gpt-4o-mini-2024-07-18":
                cost_est = self.est_openai_cost_w_cached(prompt_cost=0.150, cached_cost=0.075, output_cost=0.600,
                                                         prompt_cost_lg=0.150, cached_cost_lg=0.075, output_cost_lg=0.600,
                                                         in_t=in_t, cin_t=cin_t, out_t=out_t)
            
            
            ###### GEMINI ######
            
            ### gemini 3.6 ###
            case "gemini-3.6-flash":
                cost_est = self.est_gemini_cost(prompt_cost=1.50, output_cost=7.50,
                                                prompt_cost_lg=1.50, output_cost_lg=7.50,
                                                in_t=in_t, cin_t=cin_t, out_t=out_t)
            
            ### gemini 3.5 ###
            case "gemini-3.5-flash":
                cost_est = self.est_gemini_cost(prompt_cost=1.50, output_cost=9.00,
                                                prompt_cost_lg=1.50, output_cost_lg=9.00,
                                                in_t=in_t, cin_t=cin_t, out_t=out_t)
            
            ### gemini 3.1 ###
            case "gemini-3.1-pro-preview":
                cost_est = self.est_gemini_cost(prompt_cost=2.00, output_cost=12.00,
                                                prompt_cost_lg=4.00, output_cost_lg=18.00,
                                                in_t=in_t, cin_t=cin_t, out_t=out_t)
            case "gemini-3.1-flash-lite" | "gemini-3.1-flash-lite-preview":
                cost_est = self.est_gemini_cost(prompt_cost=0.25, output_cost=1.50,
                                                prompt_cost_lg=0.25, output_cost_lg=1.50,
                                                in_t=in_t, cin_t=cin_t, out_t=out_t)
            
            ### gemini 3.0 ###
            case "gemini-3-flash-preview":
                cost_est = self.est_gemini_cost(prompt_cost=0.50, output_cost=3.00,
                                                prompt_cost_lg=0.50, output_cost_lg=3.00,
                                                in_t=in_t, cin_t=cin_t, out_t=out_t)
            
            ### gemini 2.5 ###
            case "gemini-2.5-pro":
                cost_est = self.est_gemini_cost(prompt_cost=1.25, output_cost=10.00,
                                                prompt_cost_lg=2.50, output_cost_lg=15.00,
                                                in_t=in_t, cin_t=cin_t, out_t=out_t)
            case "gemini-2.5-flash":
                cost_est = self.est_gemini_cost(prompt_cost=0.30, output_cost=2.50,
                                                prompt_cost_lg=0.30, output_cost_lg=2.50,
                                                in_t=in_t, cin_t=cin_t, out_t=out_t)
            case "gemini-2.5-flash-lite":
                cost_est = self.est_gemini_cost(prompt_cost=0.10, output_cost=0.40,
                                                prompt_cost_lg=0.10, output_cost_lg=0.40,
                                                in_t=in_t, cin_t=cin_t, out_t=out_t)
            
            # ### gemini 2.0 ###
            # case "gemini-2.0-flash":
            #     cost_est = self.est_gemini_cost(prompt_cost=0.15, output_cost=0.60,
            #                                     prompt_cost_lg=0.15, output_cost_lg=0.60,
            #                                     in_t=in_t, cin_t=cin_t, out_t=out_t)
            # case "gemini-2.0-flash-lite":
            #     cost_est = self.est_gemini_cost(prompt_cost=0.075, output_cost=0.30,
            #                                     prompt_cost_lg=0.075, output_cost_lg=0.30,
            #                                     in_t=in_t, cin_t=cin_t, out_t=out_t)
            
            
            ###### LLAMA ######
            
            ### llama 4 ###
            case "meta/llama-4-maverick-17b-128e-instruct-maas":
                cost_est = self.est_gemini_cost(prompt_cost=0.35, output_cost=1.15,
                                                prompt_cost_lg=0.35, output_cost_lg=1.15,
                                                in_t=in_t, cin_t=cin_t, out_t=out_t)
            
            
        return cost_est


@dataclass
class APIRequest:
    """Stores an API request's inputs, outputs, and other metadata. Contains a method to make an API call."""

    task_id: int
    model_name: str
    messages: dict
    max_tokens: int = 4096
    attempts_left: int = 3
    user_tokens: int = field(default_factory=int)
    max_possible_tokens: int = field(default_factory=int)
    success: bool = False
    temperature: float = None
    top_p: float = None
    skip: bool = False


    def __post_init__(self):
        if (self.messages is not None):
            #self.user_tokens = self.num_tokens_in_messages()
            self.max_possible_tokens = self.max_tokens + self.user_tokens
        else:
            self.skip = True
    
    
    # def num_tokens_in_messages(self) -> int:
    #     """Count the number of tokens in the user request"""
        
    #     try:
    #         encoding = tiktoken.encoding_for_model(self.model_name)
    #     except:
    #         encoding = tiktoken.encoding_for_model("gpt-4o")
        
    #     num_tokens = 0
    #     for message in self.messages:
    #         num_tokens += 4  # every message follows <im_start>{role/name}\n{content}<im_end>\n
    #         if message is not None and isinstance(message, dict):
    #             for key, value in message.items():
    #                 num_tokens += len(encoding.encode(value))
    #                 if key == "name":  # if there's a name, the role is omitted
    #                     num_tokens -= 1  # role is always required and always 1 token
    #     num_tokens += 2  # every reply is primed with <im_start>assistant
        
    #     return num_tokens


    @staticmethod
    def fix_json_string(
            s: str,
    ) -> str:
        
        if s is None:
            return "{}"
        
        out = s
        
        start_i = out.find('{')
        end_i = out.rfind('}')
        if start_i != -1 and end_i != -1:
            out = s[start_i : end_i + 1]
        
        return out


    def get_api_limits(
            self,
            api_client: object,
            model_name: str,
            status_tracker: APIStatus,
    ):
        try:
            api_results_raw = api_client.chat.completions.with_raw_response.create(
                model = model_name,
                messages = [{
                    "role" : "user",
                    "content" : "respond simply with the number '1'"
                }],
                max_completion_tokens = 50,
                n = 1)
            status_tracker.max_tpm = int(api_results_raw.headers['x-ratelimit-remaining-tokens'])
            status_tracker.max_rpm = int(api_results_raw.headers['x-ratelimit-remaining-requests'])
            status_tracker.available_tpm = status_tracker.max_tpm
            status_tracker.available_rpm = status_tracker.max_rpm
            status_tracker.calculated_tpm = status_tracker.max_tpm
            status_tracker.calculated_rpm = status_tracker.max_rpm
        
            # Log tokens
            api_results = api_results_raw.parse()
            status_tracker.tokens_in += api_results.usage.prompt_tokens
            if hasattr(api_results.usage.prompt_tokens_details, "cached_tokens"):
                if api_results.usage.prompt_tokens_details.cached_tokens is not None:
                    status_tracker.cached_tokens_in += api_results.usage.prompt_tokens_details.cached_tokens
            status_tracker.tokens_out += api_results.usage.completion_tokens
            
        except openai_NotFoundError as e:
            logger.error(f"ERROR!! Model '{model_name}' not found for this server/region!: {e}")
            exit(f"ERROR!! Model '{model_name}' not found for this server/region!")
            
        except Exception as e:
            logger.error(f"ERROR!! Initial API call to get limits failed!: {e}")
            exit(f"ERROR!! Initial API call to get limits failed!: {e}")
            
    
    def get_openai_chat_response_format(
            self,
            rf: dict
    ) -> dict:
        """
        Convert a Responses-API style response_format into the Chat-Completions
        shape.
    
        Responses (json_schema):   {"type":"json_schema","name":...,"schema":...,"strict":...}
        Chat Completions:          {"type":"json_schema","json_schema":{"name":...,"schema":...,"strict":...}}
    
        {"type":"json_object"} and {"type":"text"} pass through unchanged. Returns
        None if rf is None so the caller can omit the key entirely.
        """
        if rf is None:
            return None
        if rf.get("type") == "json_schema":
            inner = {k: v for k, v in rf.items() if k != "type"}
            # If it was already nested (chat-completions style), don't double-wrap.
            if "json_schema" in inner and set(inner.keys()) == {"json_schema"}:
                return rf
            return {"type": "json_schema", "json_schema": inner}
        return rf


    def call_openai_api(
        self,
        api_client: object,
        model_name: str,
        status_tracker: APIStatus,
        result_list: list[str],
        response_format: dict = None,
        api_file_parts: dict = None,
        thinking: bool = False,
        reasoning_effort: str = "medium",
        web_search: bool = False,
    ):
        """Calls the OpenAI/Azure API serially and saves results."""
        
        error = None
        blocked = False
        temp_tokens_out = 0
        api_results = None
        
        if (self.skip == False):
            try:
                
                # Split up the messages by user/developer
                user_prompt = ""
                developer_prompt = ""
                for d in self.messages:
                    if d.get('role') == 'user':
                        user_prompt = d.get('content')
                    elif d.get('role') == 'developer':
                        developer_prompt = d.get('content')
                if api_file_parts is None:
                    api_file_parts = { "store_parts": [], "file_parts": [] }
                
                # Build arguments
                kwargs = {
                    "model": model_name,
                    #"input": self.messages,
                    "input": [
                        { "role": "developer", "content": developer_prompt },
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": user_prompt }
                            ] + api_file_parts['file_parts'],
                        },
                    ],
                    "max_output_tokens": None,
                    "text": { "format": response_format },
                    "tools": api_file_parts['store_parts'],
                }
                if thinking == True:
                    kwargs['reasoning'] = { "effort": reasoning_effort }
                else:
                    if self.top_p is not None:
                        kwargs['top_p'] = self.top_p
                    if self.temperature is not None:
                        kwargs['temperature'] = self.temperature
                if web_search == True:
                    kwargs['tools'] = kwargs['tools'] + [{"type": "web_search_preview"}]
                
                # Call API
                api_results = api_client.responses.create(**kwargs)
                
                logger.info(f"Task #{self.task_id} - api response received")
                
                # If structured JSON output, test if it parses correctly
                if response_format is not None:
                    
                    response_text = self.fix_json_string(api_results.output_text)
                    
                    # # Check for strange instance where the returned JSON restarts itself in the middle
                    # first_property = next(iter(response_format['schema']['properties']))
                    # first_property_string = '{"' + first_property + '":'
                    # last_index = text_results.rfind(first_property_string)
                    # if last_index != -1:
                    #     text_results = text_results[last_index:]
                    
                    json.loads(response_text)
                    
                # Finally, if nothing else, save text response
                else:
                    response_text = api_results.output_text
            
            # If malformed JSON response
            except (json.JSONDecodeError, json.decoder.JSONDecodeError) as e:
                logger.error(f"Task #{self.task_id} result has invalid JSON: {e}")
                #print(f"\n\tTask #{self.task_id} result has invalid JSON.\n")
                status_tracker.tokens_out += api_results.usage.output_tokens
                temp_tokens_out = api_results.usage.output_tokens
                error = e
            
            # If official RateLimitError exception was caught
            except openai_RateLimitError as e:
                logger.warning(f"Task #{self.task_id} failed with Rate Limit Error")
                print(f"\n\tTask #{self.task_id} failed with Rate Limit Error")
                error = e
            
            # If official BadRequestError exception was caught
            except openai_BadRequestError as e:
                if getattr(e, "code", "") == "content_policy_violation":
                    blocked = True
                    self.result = "BLOCKED"
                    logger.warning(f"Task #{self.task_id} WAS BLOCKED FOR UNKNOWN REASON!:\n\n{api_results}")
                    print(f"\n\tTask #{self.task_id} BLOCKED: content_policy_violation")
                    status_tracker.num_other_errors += 1
                    self.attempts_left = 0
                    error = e
                else:
                    logger.warning(f"Task #{self.task_id} failed with BadRequestError exception: {e}")
                    status_tracker.num_other_errors += 1
                    self.result = "BAD REQUEST"
                    self.attempts_left = 0
                    error = e
            
            # If unknown exception was raised
            except Exception as e:
                logger.warning(f"Task #{self.task_id} failed with unknown exception: {e}")
                error = e
        
        # If any error occurred
        if error:
            status_tracker.tokens_in += self.user_tokens
            
            # Calculate costs
            status_tracker.total_cost += status_tracker.estimate_costs(
                model_name=model_name,
                in_t=self.user_tokens,
                cin_t=0,
                out_t=temp_tokens_out,
            )
                
            # If no attempts left
            if self.attempts_left == 0:
                if blocked == False:
                    logger.error(f"Task #{self.task_id} failed after all attempts.")
                    #print(f"\n\tTask #{self.task_id} failed after all attempts.")
                    self.result = "FAILED AFTER ALL ATTEMPTS."
                result_list[self.task_id] = None
                
        # Else if SUCCESS
        else:
            
            if (self.skip == False):
                
                # Save results to list
                result_list[self.task_id] = response_text
                
                # Tokens + costs
                cached_tokens_in = 0
                reasoning_tokens_out = 0
                tokens_in = 0
                tokens_out = 0
                total_tokens = 0
                if api_results.usage is not None:
                    
                    # Cached tokens
                    if api_results.usage.input_tokens_details is not None:
                        if api_results.usage.input_tokens_details.cached_tokens is not None:
                                cached_tokens_in = api_results.usage.input_tokens_details.cached_tokens
                    
                    # Reasoning tokens
                    if api_results.usage.output_tokens_details is not None:
                        if api_results.usage.output_tokens_details.reasoning_tokens is not None:
                            reasoning_tokens_out = api_results.usage.output_tokens_details.reasoning_tokens
                    
                    # Input tokens
                    if api_results.usage.input_tokens is not None:
                            tokens_in = api_results.usage.input_tokens
                    
                    # Output tokens
                    if api_results.usage.output_tokens is not None:
                            tokens_out = api_results.usage.output_tokens
                    
                    # Total tokens
                    if api_results.usage.total_tokens is not None:
                            total_tokens = api_results.usage.total_tokens
                    
                    # Log token usage
                    status_tracker.tokens_in += tokens_in
                    status_tracker.cached_tokens_in += cached_tokens_in
                    status_tracker.tokens_out += tokens_out
                    status_tracker.reasoning_tokens_out += reasoning_tokens_out
                    status_tracker.last_request_tokens = total_tokens
                    
                    # Calculate costs
                    status_tracker.total_cost += status_tracker.estimate_costs(
                        model_name=api_results.model,
                        in_t=tokens_in,
                        cin_t=cached_tokens_in,
                        out_t=tokens_out,
                    )
                
                logger.info(
                    f"Task #{self.task_id} COMPLETE!! "
                    f"Actual Tokens Used: {total_tokens}, "
                    f"Overall Tokens Used: {status_tracker.tokens_in + status_tracker.tokens_out}"
                )
                logger.info(
                    f"Task #{self.task_id} token details: "
                    f"Input: {tokens_in}, "
                    f"Cached: {cached_tokens_in}, "
                    f"Output: {tokens_out}, "
                    f"Thinking: {reasoning_tokens_out}, "
                    f"Total: {total_tokens}"
                )
                
            else:
                
                logger.info(
                    f"Task #{self.task_id} SKIPPED!!"
                )
            
            # Update status
            self.success = True
            status_tracker.completed_tasks[self.task_id] = True
            status_tracker.num_tasks_in_progress -= 1
            status_tracker.num_tasks_succeeded += 1
            status_tracker.last_activity_time = time.time()
            

    def call_vertex_api(
        self,
        api_client: object,
        model_name: str,
        status_tracker: APIStatus,
        result_list: list[str],
        response_format: dict = None,
        api_file_parts: list = None,
        thinking: bool = False,
        reasoning_effort: str = None,
        web_search: bool = False,
    ):
        """Calls the Google Vertex API API serially and saves results."""
        
        error = None
        blocked = False
        temp_tokens_out = 0
        api_results = None
        
        if (self.skip == False):
            try:
                
                # Since this is Gemini, we need to split up the messages by user/developer
                user_prompt = ""
                developer_prompt = ""
                for d in self.messages:
                    if d.get('role') == 'user':
                        user_prompt = d.get('content')
                    elif d.get('role') == 'developer':
                        developer_prompt = d.get('content')
                if api_file_parts is None:
                    api_file_parts = []
                
                # Build arguments
                config_kwargs = {
                    "system_instruction": developer_prompt,
                    "automatic_function_calling": genai.types.AutomaticFunctionCallingConfig(disable=True),
                    "safety_settings": [
                        genai.types.SafetySetting(
                            category=genai.types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                            threshold=genai.types.HarmBlockThreshold.BLOCK_NONE,
                        ),
                        genai.types.SafetySetting(
                            category=genai.types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                            threshold=genai.types.HarmBlockThreshold.BLOCK_NONE,
                        ),
                        genai.types.SafetySetting(
                            category=genai.types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                            threshold=genai.types.HarmBlockThreshold.BLOCK_NONE,
                        ),
                        genai.types.SafetySetting(
                            category=genai.types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                            threshold=genai.types.HarmBlockThreshold.BLOCK_NONE,
                        ),
                        genai.types.SafetySetting(
                            category=genai.types.HarmCategory.HARM_CATEGORY_CIVIC_INTEGRITY,
                            threshold=genai.types.HarmBlockThreshold.BLOCK_NONE,
                        ),
                    ],
                    #"media_resolution": genai.types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
                }
                if thinking == True and reasoning_effort is not None:
                    config_kwargs['thinking_config'] = genai.types.ThinkingConfig(thinking_budget=int(reasoning_effort))
                if self.top_p is not None:
                    config_kwargs['topP'] = self.top_p
                if self.temperature is not None:
                    config_kwargs['temperature'] = self.temperature
                if response_format is not None:
                    config_kwargs['response_mime_type'] = "application/json"
                    config_kwargs['response_schema'] = response_format
                if web_search == True:
                    config_kwargs['tools'] = [ genai.types.Tool(google_search=genai.types.GoogleSearch()) ]
                configs = genai.types.GenerateContentConfig(**config_kwargs)
                kwargs = {
                    "model": model_name,
                    "contents": [
                        genai.types.Content(
                            role="user",
                            parts=[genai.types.Part.from_text(text=user_prompt)] + api_file_parts,
                        )
                    ],
                    "config": configs,
                }
                
                # Call API
                api_results = api_client.models.generate_content(**kwargs)
                
                logger.info(f"Task #{self.task_id} - api response received")
                
                # If no exception caught, but response was blocked
                if api_results.prompt_feedback is not None and api_results.prompt_feedback.block_reason is not None:
                    blocked = True
                    self.result = "BLOCKED"
                    logger.warning(f"Task #{self.task_id} WAS BLOCKED FOR UNKNOWN REASON!:\n\n{api_results.prompt_feedback.block_reason}\n\n{api_results}")
                    print(f"\n\tTask #{self.task_id} BLOCKED: {api_results.prompt_feedback.block_reason}")
                    status_tracker.num_other_errors += 1
                    self.attempts_left = 0
                    error = api_results
                
                # If finished for SAFETY reason
                elif api_results.candidates is not None and api_results.candidates[0].finish_reason is genai.types.FinishReason.SAFETY:
                    blocked = True
                    self.result = "BLOCKED"
                    logger.warning(f"Task #{self.task_id} FINISHED FOR SAFTEY REASONS!:\n\n{api_results.candidates[0].finish_reason}\n\n{api_results}")
                    print(f"\n\tTask #{self.task_id} was BLOCKED for safety reasons")
                    status_tracker.num_other_errors += 1
                    self.attempts_left = 0
                    error = api_results
                
                # Else finished for unknown finish reason
                elif api_results.candidates[0].finish_reason is not genai.types.FinishReason.STOP:
                    blocked = True
                    self.results = str(api_results.candidates[0].finish_reason)
                    logger.warning(f"Task #{self.task_id} finished with abnormal reason!: {api_results.candidates[0].finish_reason}")
                    print(f"Task #{self.task_id} finished with abnormal reason!: {api_results.candidates[0].finish_reason}")
                    status_tracker.num_other_errors += 1
                    self.attempts_left = 0
                    error = api_results
                
                # If structured JSON output, test if it parses correctly
                elif response_format is not None:
                    response_text = self.fix_json_string(api_results.text)
                    json.loads(response_text)
                    
                # Finally, if nothing else, save text response
                else:
                    response_text = api_results.text
            
            # If malformed JSON response
            except (json.JSONDecodeError, json.decoder.JSONDecodeError) as e:
                logger.error(f"Task #{self.task_id} result has invalid JSON: {e}")
                #print(f"\n\tTask #{self.task_id} result has invalid JSON.\n")
                
                temp_tokens_out = api_results.usage_metadata.candidates_token_count
                if api_results.usage_metadata.thoughts_token_count is not None:
                    temp_tokens_out += api_results.usage_metadata.thoughts_token_count
                status_tracker.tokens_out += temp_tokens_out
                
                error = e
            
            # If official Gemini ClientError exception was caught
            except genai.errors.ClientError as e:
                if e.code == 429:
                    logger.warning(f"Task #{self.task_id} failed with Gemini ResourceExhausted exception: {e}")
                    #print(f"\n\tTask #{self.task_id} failed with Gemini ResourceExhausted exception")
                    status_tracker.time_of_last_rate_limit_error = time.time()
                    status_tracker.num_rate_limit_errors += 1
                elif e.code == 403:
                    blocked = True
                    self.result = "DENIED"
                    logger.warning(f"Task #{self.task_id} failed with Gemini Permission Denied exception: {e}")
                    status_tracker.num_other_errors += 1
                    self.attempts_left = 0
                else:
                    logger.warning(f"Task #{self.task_id} failed with Gemini ClientError exception: {e}")
                    #print(f"\n\tTask #{self.task_id} failed with Gemini ClientError exception")
                    status_tracker.num_other_errors += 1
                error = e
            
            # If official Gemini APIError exception was caught
            except genai.errors.APIError as e:
                logger.warning(f"Task #{self.task_id} failed with Gemini APIError exception: {e}")
                status_tracker.num_other_errors += 1
                error = e
            
            # If unknown exception was raised
            except Exception as e:
                logger.warning(f"Task #{self.task_id} failed with unknown exception: {e}")
                error = e
        
        # If any error occurred
        if error:
            status_tracker.tokens_in += self.user_tokens
            
            # Calculate costs
            status_tracker.total_cost += status_tracker.estimate_costs(
                model_name=model_name,
                in_t=self.user_tokens,
                cin_t=0,
                out_t=temp_tokens_out,
            )
            
            # If no attempts left
            if self.attempts_left == 0:
                if blocked == False:
                    logger.error(f"Task #{self.task_id} failed after all attempts.")
                    print(f"\n\tTask #{self.task_id} failed after all attempts.")
                result_list[self.task_id] = None
                
        # Else if SUCCESS
        else:
            
            if (self.skip == False):
                
                # Save results to list
                result_list[self.task_id] = response_text
                
                # Tokens + costs
                cached_tokens_in = 0
                reasoning_tokens_out = 0
                tokens_in = 0
                tokens_out = 0
                total_tokens = 0
                if api_results.usage_metadata is not None:
                    
                    # Cached tokens                    
                    if api_results.usage_metadata.cached_content_token_count is not None:
                            cached_tokens_in = api_results.usage_metadata.cached_content_token_count
                    
                    # Reasoning tokens
                    if api_results.usage_metadata.thoughts_token_count is not None:
                        reasoning_tokens_out = api_results.usage_metadata.thoughts_token_count
                    
                    # Input tokens
                    if api_results.usage_metadata.prompt_token_count is not None:
                            tokens_in = api_results.usage_metadata.prompt_token_count
                    
                    # Output tokens
                    if api_results.usage_metadata.candidates_token_count is not None:
                            tokens_out = api_results.usage_metadata.candidates_token_count
                    
                    # Total tokens
                    if api_results.usage_metadata.total_token_count is not None:
                            total_tokens = api_results.usage_metadata.total_token_count
                    
                    # Log token usage
                    status_tracker.tokens_in += tokens_in
                    status_tracker.cached_tokens_in += cached_tokens_in
                    status_tracker.tokens_out += tokens_out + reasoning_tokens_out
                    status_tracker.reasoning_tokens_out += reasoning_tokens_out
                    status_tracker.last_request_tokens = total_tokens
                    
                    # Calculate costs
                    status_tracker.total_cost += status_tracker.estimate_costs(
                        model_name=model_name,
                        in_t=tokens_in,
                        cin_t=cached_tokens_in,
                        out_t=tokens_out + reasoning_tokens_out,
                    )
                
                logger.info(
                    f"Task #{self.task_id} COMPLETE!! "
                    f"Actual Tokens Used: {total_tokens}, "
                    f"Overall Tokens Used: {status_tracker.tokens_in + status_tracker.tokens_out}"
                )
                logger.info(
                    f"Task #{self.task_id} token details: "
                    f"Input: {tokens_in}, "
                    f"Cached: {cached_tokens_in}, "
                    f"Output: {tokens_out + reasoning_tokens_out}, "
                    f"Thinking: {reasoning_tokens_out}, "
                    f"Total: {total_tokens}"
                )
                
            else:
                
                logger.info(
                    f"Task #{self.task_id} SKIPPED!!"
                )
            
            # Update status
            self.success = True
            status_tracker.completed_tasks[self.task_id] = True
            status_tracker.num_tasks_in_progress -= 1
            status_tracker.num_tasks_succeeded += 1
            status_tracker.last_activity_time = time.time()
            
            
        def call_meta_vertex_api(
            self,
            api_client: object,
            model_name: str,
            status_tracker: APIStatus,
            result_list: list[str],
            response_format: dict = None,
            api_file_parts: dict = None,
            thinking: bool = False,
            reasoning_effort: str = None,
            web_search: bool = False,
        ):
        
            error = None
            blocked = False
            temp_tokens_out = 0
            api_results = None
        
            if (self.skip == False):
                try:
        
                    # Split up the messages by user/developer
                    user_prompt = ""
                    developer_prompt = ""
                    for d in self.messages:
                        if d.get('role') == 'user':
                            user_prompt = d.get('content')
                        elif d.get('role') == 'developer':
                            developer_prompt = d.get('content')
                    if api_file_parts is None:
                        api_file_parts = {"store_parts": [], "file_parts": []}
        
                    # Convert OpenAI Responses style prompts to OpenAI Chat Completion style messages
                    messages = []
                    if developer_prompt:
                        messages.append({"role": "system", "content": developer_prompt})
                    if api_file_parts['file_parts']:
                        user_content = [{"type": "text", "text": user_prompt}]
                        user_content += api_file_parts['file_parts']
                        messages.append({"role": "user", "content": user_content})
                    else:
                        # plain string content is simplest and fully supported
                        messages.append({"role": "user", "content": user_prompt})
                        
                    # Build arguments
                    kwargs = {
                        "model": model_name,
                        "messages": messages,
                        "max_tokens": None,
                    }
                    
                    # Convert OpenAI Responses style formats to OpenAI Chat Completion style formats
                    if response_format is not None:
                        cc_response_format = self.get_openai_chat_response_format(response_format)
                        kwargs["response_format"] = cc_response_format
                        
                    if thinking == True:
                        kwargs['reasoning'] = { "effort": reasoning_effort }
                    else:
                        if self.top_p is not None:
                            kwargs['top_p'] = self.top_p
                        if self.temperature is not None:
                            kwargs['temperature'] = self.temperature
                    
                    if web_search == True:
                        logger.warning(
                            f"Task #{self.task_id} - web_search requested but not supported "
                            f"for Vertex Llama MaaS; ignoring."
                        )
                    
                    # Call API
                    api_results = api_client.responses.create(**kwargs)
        
                    logger.info(f"Task #{self.task_id} - api response received")
        
                    choice = api_results.choices[0]
        
                    # Safety filtering: if Llama Guard is enabled on the endpoint, MaaS
                    # returns finish_reason == "content_filtered" (and/or a refusal)
                    # instead of raising. Treat it like a content block.
                    # (Note: Llama 4 MaaS does NOT run Llama Guard by default.)
                    refusal = getattr(choice.message, "refusal", None)
                    if getattr(choice, "finish_reason", None) == "content_filtered" or refusal:
                        blocked = True
                        self.result = "BLOCKED"
                        self.attempts_left = 0
                        logger.warning(
                            f"Task #{self.task_id} WAS BLOCKED (content_filtered/refusal): {refusal}"
                        )
                        print(f"\n\tTask #{self.task_id} BLOCKED: content_filtered")
                        status_tracker.num_other_errors += 1
                        error = RuntimeError("content_filtered")
        
                    else:
                        # If structured JSON output, test if it parses correctly
                        if response_format is not None:
        
                            response_text = self.fix_json_string(choice.message.content)
        
                            json.loads(response_text)
        
                        # Finally, if nothing else, save text response
                        else:
                            response_text = choice.message.content
        
                # If malformed JSON response
                except (json.JSONDecodeError, json.decoder.JSONDecodeError) as e:
                    logger.error(f"Task #{self.task_id} result has invalid JSON: {e}")
                    _ct = 0
                    if api_results is not None and api_results.usage is not None:
                        _ct = getattr(api_results.usage, "completion_tokens", 0) or 0
                    status_tracker.tokens_out += _ct
                    temp_tokens_out = _ct
                    error = e
        
                # If official RateLimitError exception was caught
                except openai_RateLimitError as e:
                    logger.warning(f"Task #{self.task_id} failed with Rate Limit Error")
                    print(f"\n\tTask #{self.task_id} failed with Rate Limit Error")
                    error = e
        
                # If official BadRequestError exception was caught
                except openai_BadRequestError as e:
                    if getattr(e, "code", "") == "content_policy_violation":
                        blocked = True
                        self.result = "BLOCKED"
                        logger.warning(f"Task #{self.task_id} WAS BLOCKED FOR UNKNOWN REASON!:\n\n{api_results}")
                        print(f"\n\tTask #{self.task_id} BLOCKED: content_policy_violation")
                        status_tracker.num_other_errors += 1
                        self.attempts_left = 0
                        error = e
                    else:
                        logger.warning(f"Task #{self.task_id} failed with BadRequestError exception: {e}")
                        status_tracker.num_other_errors += 1
                        self.result = "BAD REQUEST"
                        self.attempts_left = 0
                        error = e
        
                # If unknown exception was raised
                except Exception as e:
                    logger.warning(f"Task #{self.task_id} failed with unknown exception: {e}")
                    error = e
        
            # If any error occurred
            if error:
                status_tracker.tokens_in += self.user_tokens
        
                # Calculate costs
                status_tracker.total_cost += status_tracker.estimate_costs(
                    model_name=model_name,
                    in_t=self.user_tokens,
                    cin_t=0,
                    out_t=temp_tokens_out,
                )
        
                # If no attempts left
                if self.attempts_left == 0:
                    if blocked == False:
                        logger.error(f"Task #{self.task_id} failed after all attempts.")
                        #print(f"\n\tTask #{self.task_id} failed after all attempts.")
                        self.result = "FAILED AFTER ALL ATTEMPTS."
                        result_list[self.task_id] = None
        
            # Else if SUCCESS
            else:
        
                if (self.skip == False):
        
                    # Save results to list
                    result_list[self.task_id] = response_text
        
                    # Tokens + costs
                    cached_tokens_in = 0
                    reasoning_tokens_out = 0
                    tokens_in = 0
                    tokens_out = 0
                    total_tokens = 0
                    if api_results.usage is not None:
        
                        u = api_results.usage
        
                        # Cached tokens (usually absent on Vertex Llama)
                        ptd = getattr(u, "prompt_tokens_details", None)
                        if ptd is not None and getattr(ptd, "cached_tokens", None) is not None:
                            cached_tokens_in = ptd.cached_tokens
        
                        # Reasoning tokens (absent for Llama; present for OpenAI reasoning models)
                        ctd = getattr(u, "completion_tokens_details", None)
                        if ctd is not None and getattr(ctd, "reasoning_tokens", None) is not None:
                            reasoning_tokens_out = ctd.reasoning_tokens
        
                        # Input tokens
                        if getattr(u, "prompt_tokens", None) is not None:
                            tokens_in = u.prompt_tokens
        
                        # Output tokens
                        if getattr(u, "completion_tokens", None) is not None:
                            tokens_out = u.completion_tokens
        
                        # Total tokens
                        if getattr(u, "total_tokens", None) is not None:
                            total_tokens = u.total_tokens
        
                        # Log token usage
                        status_tracker.tokens_in += tokens_in
                        status_tracker.cached_tokens_in += cached_tokens_in
                        status_tracker.tokens_out += tokens_out
                        status_tracker.reasoning_tokens_out += reasoning_tokens_out
                        status_tracker.last_request_tokens = total_tokens
        
                        # Calculate costs
                        status_tracker.total_cost += status_tracker.estimate_costs(
                            model_name=api_results.model,
                            in_t=tokens_in,
                            cin_t=cached_tokens_in,
                            out_t=tokens_out,
                        )
        
                    logger.info(
                        f"Task #{self.task_id} COMPLETE!! "
                        f"Actual Tokens Used: {total_tokens}, "
                        f"Overall Tokens Used: {status_tracker.tokens_in + status_tracker.tokens_out}"
                    )
                    logger.info(
                        f"Task #{self.task_id} token details: "
                        f"Input: {tokens_in}, "
                        f"Cached: {cached_tokens_in}, "
                        f"Output: {tokens_out}, "
                        f"Thinking: {reasoning_tokens_out}, "
                        f"Total: {total_tokens}"
                    )
        
                else:
                    
                    logger.info(
                        f"Task #{self.task_id} SKIPPED!!"
                    )
        
                # Update status
                self.success = True
                status_tracker.completed_tasks[self.task_id] = True
                status_tracker.num_tasks_in_progress -= 1
                status_tracker.num_tasks_succeeded += 1
                status_tracker.last_activity_time = time.time()
                
    
    async def call_openai_api_parallel(
        self,
        api_client: object,
        model_name: str,
        status_tracker: APIStatus,
        retry_queue: asyncio.Queue,
        result_list: list[str],
        timeout: int = 300,
        response_format: dict = None,
        api_file_parts: dict = None,
        thinking: bool = False,
        reasoning_effort: str = "medium",
        web_search: bool = False,
    ):
        """Calls the OpenAI/Azure API asychronously and saves results."""
        
        error = None
        blocked = False
        temp_tokens_out = 0
        api_results = None
        
        if (self.skip == False):
            try:
                
                # Split up the messages by user/developer
                user_prompt = ""
                developer_prompt = ""
                for d in self.messages:
                    if d.get('role') == 'user':
                        user_prompt = d.get('content')
                    elif d.get('role') == 'developer':
                        developer_prompt = d.get('content')
                if api_file_parts is None:
                    api_file_parts = { "store_parts": [], "file_parts": [] }
                
                # Build arguments
                kwargs = {
                    "model": model_name,
                    #"input": self.messages,
                    "input": [
                        { "role": "developer", "content": developer_prompt },
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": user_prompt }
                            ] + api_file_parts['file_parts'],
                        },
                    ],
                    "max_output_tokens": None,
                    "text": { "format": response_format },
                    "tools": api_file_parts['store_parts'],
                }
                if thinking == True:
                    kwargs['reasoning'] = { "effort": reasoning_effort }
                else:
                    if self.top_p is not None:
                        kwargs['top_p'] = self.top_p
                    if self.temperature is not None:
                        kwargs['temperature'] = self.temperature
                if web_search == True:
                    kwargs['tools'] = kwargs['tools'] + [{"type": "web_search_preview"}]
                
                # Call API
                api_results = await asyncio.wait_for(
                    api_client.responses.create(**kwargs),
                    timeout=timeout,
                )
                
                logger.info(f"Task #{self.task_id} - api response received")
                
                # If structured JSON output, test if it parses correctly
                if response_format is not None:
                    
                    response_text = self.fix_json_string(api_results.output_text)
                    
                    # # Check for strange instance where the returned JSON restarts itself in the middle
                    # first_property = next(iter(response_format['schema']['properties']))
                    # first_property_string = '{"' + first_property + '":'
                    # last_index = text_results.rfind(first_property_string)
                    # if last_index != -1:
                    #     text_results = text_results[last_index:]
                    
                    json.loads(response_text)
                
                # Finally, if nothing else, save text response
                else:
                    response_text = api_results.output_text
                
            # If asyncio threw time out exception
            except (TimeoutError, asyncio.TimeoutError) as e:
                logger.warning(f"Task #{self.task_id} timed out with Exception {e}")
                #print(f"\n\tTask #{self.task_id} timed out\n")
                status_tracker.time_of_last_rate_limit_error = time.time()
                status_tracker.num_rate_limit_errors += 1
                error = e
            
            # If malformed JSON response
            except (json.JSONDecodeError, json.decoder.JSONDecodeError) as e:
                logger.error(f"Task #{self.task_id} result has invalid JSON: {e}")
                #print(f"\n\tTask #{self.task_id} result has invalid JSON.\n")
                status_tracker.num_other_errors += 1
                status_tracker.tokens_out += api_results.usage.output_tokens
                temp_tokens_out = api_results.usage.output_tokens
                error = e
            
            # If official RateLimitError exception was caught
            except openai_RateLimitError as e:
                logger.warning(f"Task #{self.task_id} failed with Rate Limit Error")
                print(f"\n\tTask #{self.task_id} failed with Rate Limit Error")
                status_tracker.time_of_last_rate_limit_error = time.time()
                status_tracker.num_rate_limit_errors += 1
                error = e
            
            # If official BadRequestError exception was caught
            except openai_BadRequestError as e:
                if getattr(e, "code", "") == "content_policy_violation":
                    blocked = True
                    self.result = "BLOCKED"
                    logger.warning(f"Task #{self.task_id} WAS BLOCKED FOR UNKNOWN REASON!:\n\n{api_results}")
                    print(f"\n\tTask #{self.task_id} BLOCKED: content_policy_violation")
                    status_tracker.num_other_errors += 1
                    self.attempts_left = 0
                    error = e
                else:
                    logger.warning(f"Task #{self.task_id} failed with BadRequestError exception: {e}")
                    status_tracker.num_other_errors += 1
                    self.result = "BAD REQUEST"
                    self.attempts_left = 0
                    error = e
            
            # If unknown exception was raised
            except Exception as e:
                logger.warning(f"Task #{self.task_id} failed with unknown exception: {e}")
                status_tracker.num_other_errors += 1
                error = e
        
        # If any error occurred, re-add task to the queue
        if error:
            status_tracker.tokens_in += self.user_tokens
            
            # Calculate costs
            status_tracker.total_cost += status_tracker.estimate_costs(
                model_name=model_name,
                in_t=self.user_tokens,
                cin_t=0,
                out_t=temp_tokens_out,
            )
            
            # If attempts left, add to the retry queue
            if self.attempts_left:
                retry_queue.put_nowait(self)
                
            # Else if failed after all attempts
            else:
                if blocked == False:
                    logger.error(f"Task #{self.task_id} failed after all attempts.")
                    print(f"\n\tTask #{self.task_id} failed after all attempts.")
                    self.result = "FAILED AFTER ALL ATTEMPTS."
                status_tracker.num_tasks_in_progress -= 1
                status_tracker.num_tasks_failed += 1
                result_list[self.task_id] = None
                
        # Else if SUCCESS
        else:
            
            if (self.skip == False):
                
                # Save results to list
                result_list[self.task_id] = response_text
                
                # Tokens + costs
                cached_tokens_in = 0
                reasoning_tokens_out = 0
                tokens_in = 0
                tokens_out = 0
                total_tokens = 0
                if api_results.usage is not None:
                    
                    # Cached tokens
                    if api_results.usage.input_tokens_details is not None:
                        if api_results.usage.input_tokens_details.cached_tokens is not None:
                                cached_tokens_in = api_results.usage.input_tokens_details.cached_tokens
                    
                    # Reasoning tokens
                    if api_results.usage.output_tokens_details is not None:
                        if api_results.usage.output_tokens_details.reasoning_tokens is not None:
                            reasoning_tokens_out = api_results.usage.output_tokens_details.reasoning_tokens
                    
                    # Input tokens
                    if api_results.usage.input_tokens is not None:
                            tokens_in = api_results.usage.input_tokens
                    
                    # Output tokens
                    if api_results.usage.output_tokens is not None:
                            tokens_out = api_results.usage.output_tokens
                    
                    # Total tokens
                    if api_results.usage.total_tokens is not None:
                            total_tokens = api_results.usage.total_tokens
                    
                    # Log token usage
                    status_tracker.tokens_in += tokens_in
                    status_tracker.cached_tokens_in += cached_tokens_in
                    status_tracker.tokens_out += tokens_out
                    status_tracker.reasoning_tokens_out += reasoning_tokens_out
                    status_tracker.last_request_tokens = total_tokens
                    
                    # Calculate costs
                    status_tracker.total_cost += status_tracker.estimate_costs(
                        model_name=api_results.model,
                        in_t=tokens_in,
                        cin_t=cached_tokens_in,
                        out_t=tokens_out,
                    )
                
                logger.info(
                    f"Task #{self.task_id} COMPLETE!! "
                    f"Actual Tokens Used: {total_tokens}, "
                    f"Overall Tokens Used: {status_tracker.tokens_in + status_tracker.tokens_out}"
                )
                logger.info(
                    f"Task #{self.task_id} token details: "
                    f"Input: {tokens_in}, "
                    f"Cached: {cached_tokens_in}, "
                    f"Output: {tokens_out}, "
                    f"Thinking: {reasoning_tokens_out}, "
                    f"Total: {total_tokens}"
                )
                
            else:
                
                result_list[self.task_id] = None
                
                logger.info(
                    f"Task #{self.task_id} SKIPPED!!"
                )
            
            # Update status
            self.success = True
            status_tracker.completed_tasks[self.task_id] = True
            status_tracker.num_tasks_in_progress -= 1
            status_tracker.num_tasks_succeeded += 1
            status_tracker.last_activity_time = time.time()


    async def call_vertex_api_parallel(
        self,
        api_client: object,
        model_name: str,
        status_tracker: APIStatus,
        retry_queue: asyncio.Queue,
        result_list: list[str],
        timeout: int = 300,
        response_format: Type[BaseModel] = None,
        api_file_parts: list = None,
        thinking: bool = False,
        reasoning_effort: str = None,
        web_search: bool = False,
    ):
        """Calls the Google Vertex API asychronously and saves results."""
        
        error = None
        blocked = False
        temp_tokens_out = 0
        api_results = None
        
        if (self.skip == False):
            try:
                
                # Since this is Gemini, we need to split up the messages by user/developer
                user_prompt = ""
                developer_prompt = ""
                for d in self.messages:
                    if d.get('role') == 'user':
                        user_prompt = d.get('content')
                    elif d.get('role') == 'developer':
                        developer_prompt = d.get('content')
                if api_file_parts is None:
                    api_file_parts = []
                
                # Build arguments
                config_kwargs = {
                    "system_instruction": developer_prompt,
                    "automatic_function_calling": genai.types.AutomaticFunctionCallingConfig(disable=True),
                    "safety_settings": [
                        genai.types.SafetySetting(
                            category=genai.types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                            threshold=genai.types.HarmBlockThreshold.BLOCK_NONE,
                        ),
                        genai.types.SafetySetting(
                            category=genai.types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                            threshold=genai.types.HarmBlockThreshold.BLOCK_NONE,
                        ),
                        genai.types.SafetySetting(
                            category=genai.types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                            threshold=genai.types.HarmBlockThreshold.BLOCK_NONE,
                        ),
                        genai.types.SafetySetting(
                            category=genai.types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                            threshold=genai.types.HarmBlockThreshold.BLOCK_NONE,
                        ),
                        genai.types.SafetySetting(
                            category=genai.types.HarmCategory.HARM_CATEGORY_CIVIC_INTEGRITY,
                            threshold=genai.types.HarmBlockThreshold.BLOCK_NONE,
                        ),
                    ],
                    #"media_resolution": genai.types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
                }
                if thinking == True and reasoning_effort is not None:
                    config_kwargs['thinking_config'] = genai.types.ThinkingConfig(thinking_budget=int(reasoning_effort))
                if self.top_p is not None:
                    config_kwargs['topP'] = self.top_p
                if self.temperature is not None:
                    config_kwargs['temperature'] = self.temperature
                if response_format is not None:
                    config_kwargs['response_mime_type'] = "application/json"
                    config_kwargs['response_schema'] = response_format
                if web_search == True:
                    config_kwargs['tools'] = [ genai.types.Tool(google_search=genai.types.GoogleSearch()) ]
                configs = genai.types.GenerateContentConfig(**config_kwargs)
                kwargs = {
                    "model": model_name,
                    "contents": [
                        genai.types.Content(
                            role="user",
                            parts=[genai.types.Part.from_text(text=user_prompt)] + api_file_parts,
                        )
                    ],
                    "config": configs,
                }
                
                # Call API
                api_results = await asyncio.wait_for(
                    api_client.aio.models.generate_content(**kwargs),
                    timeout=timeout)
                
                logger.info(f"Task #{self.task_id} - api response received")
                
                # If no exception caught, but response was blocked
                if api_results.prompt_feedback is not None and api_results.prompt_feedback.block_reason is not None:
                    blocked = True
                    self.result = "BLOCKED"
                    logger.warning(f"Task #{self.task_id} WAS BLOCKED FOR UNKNOWN REASON!:\n\n{api_results.prompt_feedback.block_reason}\n\n{api_results}")
                    print(f"\n\tTask #{self.task_id} BLOCKED: {api_results.prompt_feedback.block_reason}")
                    status_tracker.num_other_errors += 1
                    self.attempts_left = 0
                    error = api_results
                
                # If finished for SAFETY reason
                elif api_results.candidates is not None and api_results.candidates[0].finish_reason is genai.types.FinishReason.SAFETY:
                    blocked = True
                    self.result = "BLOCKED"
                    logger.warning(f"Task #{self.task_id} FINISHED FOR SAFTEY REASONS!:\n\n{api_results.candidates[0].finish_reason}\n\n{api_results}")
                    print(f"\n\tTask #{self.task_id} was BLOCKED for safety reasons")
                    status_tracker.num_other_errors += 1
                    self.attempts_left = 0
                    error = api_results
                
                # Else finished for unknown finish reason
                elif api_results.candidates[0].finish_reason is not genai.types.FinishReason.STOP:
                    blocked = True
                    self.results = str(api_results.candidates[0].finish_reason)
                    logger.warning(f"Task #{self.task_id} finished with abnormal reason!: {api_results.candidates[0].finish_reason}")
                    print(f"Task #{self.task_id} finished with abnormal reason!: {api_results.candidates[0].finish_reason}")
                    status_tracker.num_other_errors += 1
                    self.attempts_left = 0
                    error = api_results
                
                # If structured JSON output, test if it parses correctly
                elif response_format is not None:
                    response_text = self.fix_json_string(api_results.text)
                    json.loads(response_text)
                
                # Finally, if nothing else, save text response
                else:
                    response_text = api_results.text
                        
            # If asyncio threw time out exception
            except (TimeoutError, asyncio.TimeoutError) as e:
                logger.warning(f"Task #{self.task_id} timed out with Exception {e}")
                #print(f"\n\tTask #{self.task_id} timed out\n")
                status_tracker.time_of_last_rate_limit_error = time.time()
                status_tracker.num_rate_limit_errors += 1
                error = e
            
            # If malformed JSON response
            except (json.JSONDecodeError, json.decoder.JSONDecodeError) as e:
                logger.error(f"Task #{self.task_id} result has invalid JSON: {e}")
                #print(f"\n\tTask #{self.task_id} result has invalid JSON.\n")
                status_tracker.num_other_errors += 1
                
                temp_tokens_out = api_results.usage_metadata.candidates_token_count
                if api_results.usage_metadata.thoughts_token_count is not None:
                    temp_tokens_out += api_results.usage_metadata.thoughts_token_count
                status_tracker.tokens_out += temp_tokens_out
                
                error = e
            
            # If official Gemini ClientError exception was caught
            except genai.errors.ClientError as e:
                if e.code == 429:
                    logger.warning(f"Task #{self.task_id} failed with Gemini ResourceExhausted exception: {e}")
                    #print(f"\n\tTask #{self.task_id} failed with Gemini ResourceExhausted exception")
                    status_tracker.time_of_last_rate_limit_error = time.time()
                    status_tracker.num_rate_limit_errors += 1
                elif e.code == 403:
                    blocked = True
                    self.result = "DENIED"
                    logger.warning(f"Task #{self.task_id} failed with Gemini Permission Denied exception: {e}")
                    status_tracker.num_other_errors += 1
                    self.attempts_left = 0
                else:
                    logger.warning(f"Task #{self.task_id} failed with Gemini ClientError exception: {e}")
                    #print(f"\n\tTask #{self.task_id} failed with Gemini ClientError exception")
                    status_tracker.num_other_errors += 1
                error = e
            
            # If official Gemini APIError exception was caught
            except genai.errors.APIError as e:
                logger.warning(f"Task #{self.task_id} failed with Gemini APIError exception: {e}")
                status_tracker.num_other_errors += 1
                error = e
            
            # If unknown exception was raised
            except Exception as e:
                logger.warning(f"Task #{self.task_id} failed with unknown exception: {e}")
                status_tracker.num_other_errors += 1
                error = e
        
        # If any error occurred, re-add task to the queue
        if error:
            status_tracker.tokens_in += self.user_tokens
            
            # Calculate costs
            status_tracker.total_cost += status_tracker.estimate_costs(
                model_name=model_name,
                in_t=self.user_tokens,
                cin_t=0,
                out_t=temp_tokens_out,
            )
            
            # If attempts left, add to the retry queue
            if self.attempts_left:
                retry_queue.put_nowait(self)
                
            # Else if failed after all attempts
            else:
                if blocked == False:
                    logger.error(f"Task #{self.task_id} failed after all attempts.")
                    print(f"\n\tTask #{self.task_id} failed after all attempts.")
                    self.result = "FAILED AFTER ALL ATTEMPTS."
                status_tracker.num_tasks_in_progress -= 1
                status_tracker.num_tasks_failed += 1
                result_list[self.task_id] = None
                
        # Else if SUCCESS
        else:
            
            if (self.skip == False):
                
                # Save results to list
                result_list[self.task_id] = response_text
                
                # Tokens + costs
                cached_tokens_in = 0
                reasoning_tokens_out = 0
                tokens_in = 0
                tokens_out = 0
                total_tokens = 0
                if api_results.usage_metadata is not None:
                    
                    # Cached tokens                    
                    if api_results.usage_metadata.cached_content_token_count is not None:
                            cached_tokens_in = api_results.usage_metadata.cached_content_token_count
                    
                    # Reasoning tokens
                    if api_results.usage_metadata.thoughts_token_count is not None:
                        reasoning_tokens_out = api_results.usage_metadata.thoughts_token_count
                    
                    # Input tokens
                    if api_results.usage_metadata.prompt_token_count is not None:
                            tokens_in = api_results.usage_metadata.prompt_token_count
                    
                    # Output tokens
                    if api_results.usage_metadata.candidates_token_count is not None:
                            tokens_out = api_results.usage_metadata.candidates_token_count
                    
                    # Total tokens
                    if api_results.usage_metadata.total_token_count is not None:
                            total_tokens = api_results.usage_metadata.total_token_count
                    
                    # Log token usage
                    status_tracker.tokens_in += tokens_in
                    status_tracker.cached_tokens_in += cached_tokens_in
                    status_tracker.tokens_out += tokens_out + reasoning_tokens_out
                    status_tracker.reasoning_tokens_out += reasoning_tokens_out
                    status_tracker.last_request_tokens = total_tokens
                    
                    # Calculate costs
                    status_tracker.total_cost += status_tracker.estimate_costs(
                        model_name=model_name,
                        in_t=tokens_in,
                        cin_t=cached_tokens_in,
                        out_t=tokens_out + reasoning_tokens_out,
                    )
                
                logger.info(
                    f"Task #{self.task_id} COMPLETE!! "
                    f"Actual Tokens Used: {total_tokens}, "
                    f"Overall Tokens Used: {status_tracker.tokens_in + status_tracker.tokens_out}"
                )
                logger.info(
                    f"Task #{self.task_id} token details: "
                    f"Input: {tokens_in}, "
                    f"Cached: {cached_tokens_in}, "
                    f"Output: {tokens_out + reasoning_tokens_out}, "
                    f"Thinking: {reasoning_tokens_out}, "
                    f"Total: {total_tokens}"
                )
                
            else:
                
                result_list[self.task_id] = None
                
                logger.info(
                    f"Task #{self.task_id} SKIPPED!!"
                )
            
            # Update status
            self.success = True
            status_tracker.completed_tasks[self.task_id] = True
            status_tracker.num_tasks_in_progress -= 1
            status_tracker.num_tasks_succeeded += 1
            status_tracker.last_activity_time = time.time()
            
    
    async def call_meta_vertex_api_parallel(
        self,
        api_client: object,
        model_name: str,
        status_tracker: APIStatus,
        retry_queue: asyncio.Queue,
        result_list: list[str],
        timeout: int = 300,
        response_format: dict = None,
        api_file_parts: dict = None,
        thinking: bool = False,
        reasoning_effort: str = None,
        web_search: bool = False,
    ):
    
        error = None
        blocked = False
        temp_tokens_out = 0
        api_results = None
    
        if (self.skip == False):
            try:
    
                # Split up the messages by user/developer
                user_prompt = ""
                developer_prompt = ""
                for d in self.messages:
                    if d.get('role') == 'user':
                        user_prompt = d.get('content')
                    elif d.get('role') == 'developer':
                        developer_prompt = d.get('content')
                if api_file_parts is None:
                    api_file_parts = {"store_parts": [], "file_parts": []}
    
                # Convert OpenAI Responses style prompts to OpenAI Chat Completion style messages
                messages = []
                if developer_prompt:
                    messages.append({"role": "system", "content": developer_prompt})
                if api_file_parts['file_parts']:
                    user_content = [{"type": "text", "text": user_prompt}]
                    user_content += api_file_parts['file_parts']
                    messages.append({"role": "user", "content": user_content})
                else:
                    # plain string content is simplest and fully supported
                    messages.append({"role": "user", "content": user_prompt})
    
                # Build arguments
                kwargs = {
                    "model": model_name,
                    "messages": messages,
                    "max_tokens": None,
                }
    
                # Convert OpenAI Responses style formats to OpenAI Chat Completion style formats
                if response_format is not None:
                    cc_response_format = self.get_openai_chat_response_format(response_format)
                    kwargs["response_format"] = cc_response_format
                    
                if thinking == True:
                    kwargs['reasoning'] = { "effort": reasoning_effort }
                else:
                    if self.top_p is not None:
                        kwargs['top_p'] = self.top_p
                    if self.temperature is not None:
                        kwargs['temperature'] = self.temperature
                
                if web_search == True:
                    logger.warning(
                        f"Task #{self.task_id} - web_search requested but not supported "
                        f"for Vertex Llama MaaS; ignoring."
                    )
                
                if self.top_p is not None:
                    kwargs['top_p'] = self.top_p
                if self.temperature is not None:
                    kwargs['temperature'] = self.temperature
    
                # Call API
                api_results = await asyncio.wait_for(
                    api_client.chat.completions.create(**kwargs),
                    timeout=timeout,
                )
    
                logger.info(f"Task #{self.task_id} - api response received")
    
                choice = api_results.choices[0]
    
                # Safety filtering: if Llama Guard is enabled on the endpoint, MaaS
                # returns finish_reason == "content_filtered" (and/or a refusal)
                # instead of raising. Treat it like a content block.
                # (Note: Llama 4 MaaS does NOT run Llama Guard by default.)
                refusal = getattr(choice.message, "refusal", None)
                if getattr(choice, "finish_reason", None) == "content_filtered" or refusal:
                    blocked = True
                    self.result = "BLOCKED"
                    self.attempts_left = 0
                    logger.warning(
                        f"Task #{self.task_id} WAS BLOCKED (content_filtered/refusal): {refusal}"
                    )
                    print(f"\n\tTask #{self.task_id} BLOCKED: content_filtered")
                    status_tracker.num_other_errors += 1
                    error = RuntimeError("content_filtered")
    
                else:
                    # If structured JSON output, test if it parses correctly
                    if response_format is not None:
    
                        response_text = self.fix_json_string(choice.message.content)
    
                        json.loads(response_text)
    
                    # Finally, if nothing else, save text response
                    else:
                        response_text = choice.message.content
    
            # If asyncio threw time out exception
            except (TimeoutError, asyncio.TimeoutError) as e:
                logger.warning(f"Task #{self.task_id} timed out with Exception {e}")
                status_tracker.time_of_last_rate_limit_error = time.time()
                status_tracker.num_rate_limit_errors += 1
                error = e
    
            # If malformed JSON response
            except (json.JSONDecodeError, json.decoder.JSONDecodeError) as e:
                logger.error(f"Task #{self.task_id} result has invalid JSON: {e}")
                status_tracker.num_other_errors += 1
                _ct = 0
                if api_results is not None and api_results.usage is not None:
                    _ct = getattr(api_results.usage, "completion_tokens", 0) or 0
                status_tracker.tokens_out += _ct
                temp_tokens_out = _ct
                error = e
    
            # If official RateLimitError exception was caught
            except openai_RateLimitError as e:
                logger.warning(f"Task #{self.task_id} failed with Rate Limit Error")
                print(f"\n\tTask #{self.task_id} failed with Rate Limit Error")
                status_tracker.time_of_last_rate_limit_error = time.time()
                status_tracker.num_rate_limit_errors += 1
                error = e
    
            # If official BadRequestError exception was caught
            except openai_BadRequestError as e:
                if getattr(e, "code", "") == "content_policy_violation":
                    blocked = True
                    self.result = "BLOCKED"
                    logger.warning(f"Task #{self.task_id} WAS BLOCKED FOR UNKNOWN REASON!:\n\n{api_results}")
                    print(f"\n\tTask #{self.task_id} BLOCKED: content_policy_violation")
                    status_tracker.num_other_errors += 1
                    self.attempts_left = 0
                    error = e
                else:
                    logger.warning(f"Task #{self.task_id} failed with BadRequestError exception: {e}")
                    status_tracker.num_other_errors += 1
                    self.result = "BAD REQUEST"
                    self.attempts_left = 0
                    error = e
    
            # If unknown exception was raised
            except Exception as e:
                logger.warning(f"Task #{self.task_id} failed with unknown exception: {e}")
                status_tracker.num_other_errors += 1
                error = e
    
        # If any error occurred, re-add task to the queue
        if error:
            status_tracker.tokens_in += self.user_tokens
    
            # Calculate costs
            status_tracker.total_cost += status_tracker.estimate_costs(
                model_name=model_name,
                in_t=self.user_tokens,
                cin_t=0,
                out_t=temp_tokens_out,
            )
    
            # If attempts left, add to the retry queue
            if self.attempts_left:
                retry_queue.put_nowait(self)
    
            # Else if failed after all attempts
            else:
                if blocked == False:
                    logger.error(f"Task #{self.task_id} failed after all attempts.")
                    print(f"\n\tTask #{self.task_id} failed after all attempts.")
                    self.result = "FAILED AFTER ALL ATTEMPTS."
                status_tracker.num_tasks_in_progress -= 1
                status_tracker.num_tasks_failed += 1
                result_list[self.task_id] = None
    
        # Else if SUCCESS
        else:
    
            if (self.skip == False):
    
                # Save results to list
                result_list[self.task_id] = response_text
    
                # Tokens + costs
                cached_tokens_in = 0
                reasoning_tokens_out = 0
                tokens_in = 0
                tokens_out = 0
                total_tokens = 0
                if api_results.usage is not None:
    
                    u = api_results.usage
    
                    # Cached tokens (usually absent on Vertex Llama)
                    ptd = getattr(u, "prompt_tokens_details", None)
                    if ptd is not None and getattr(ptd, "cached_tokens", None) is not None:
                        cached_tokens_in = ptd.cached_tokens
    
                    # Reasoning tokens (absent for Llama; present for OpenAI reasoning models)
                    ctd = getattr(u, "completion_tokens_details", None)
                    if ctd is not None and getattr(ctd, "reasoning_tokens", None) is not None:
                        reasoning_tokens_out = ctd.reasoning_tokens
    
                    # Input tokens
                    if getattr(u, "prompt_tokens", None) is not None:
                        tokens_in = u.prompt_tokens
    
                    # Output tokens
                    if getattr(u, "completion_tokens", None) is not None:
                        tokens_out = u.completion_tokens
    
                    # Total tokens
                    if getattr(u, "total_tokens", None) is not None:
                        total_tokens = u.total_tokens
    
                    # Log token usage
                    status_tracker.tokens_in += tokens_in
                    status_tracker.cached_tokens_in += cached_tokens_in
                    status_tracker.tokens_out += tokens_out
                    status_tracker.reasoning_tokens_out += reasoning_tokens_out
                    status_tracker.last_request_tokens = total_tokens
    
                    # Calculate costs
                    status_tracker.total_cost += status_tracker.estimate_costs(
                        model_name=api_results.model,
                        in_t=tokens_in,
                        cin_t=cached_tokens_in,
                        out_t=tokens_out,
                    )
    
                logger.info(
                    f"Task #{self.task_id} COMPLETE!! "
                    f"Actual Tokens Used: {total_tokens}, "
                    f"Overall Tokens Used: {status_tracker.tokens_in + status_tracker.tokens_out}"
                )
                logger.info(
                    f"Task #{self.task_id} token details: "
                    f"Input: {tokens_in}, "
                    f"Cached: {cached_tokens_in}, "
                    f"Output: {tokens_out}, "
                    f"Thinking: {reasoning_tokens_out}, "
                    f"Total: {total_tokens}"
                )
    
            else:
    
                result_list[self.task_id] = None
    
                logger.info(
                    f"Task #{self.task_id} SKIPPED!!"
                )
    
            # Update status
            self.success = True
            status_tracker.completed_tasks[self.task_id] = True
            status_tracker.num_tasks_in_progress -= 1
            status_tracker.num_tasks_succeeded += 1
            status_tracker.last_activity_time = time.time()

