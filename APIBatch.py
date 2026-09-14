# -*- coding: utf-8 -*-
"""
@author: mmorrell
Version:  2.3.0
Version Creation: 07/06/2026
"""


# imports
import colorama
from copy import deepcopy
from dataclasses import (
    dataclass,
    field,
)
from google import genai
from google.cloud import storage as google_storage
import gzip
import json
import logging
from openai import OpenAI
from openai.types.responses.response import Response as openai_response
import os
import pathlib
import re
import sys
import time


# local imports
from APIRequest import (
    APIRequest,
    APIStatus,
)


# logging
logger = logging.getLogger(__name__)


@dataclass
class APIBatch:
    """ Temporary """

    id: int = field(default_factory=int)
    name: str = field(default_factory=str)
    file_name: str = field(default_factory=str)
    description: str = field(default_factory=str)
    api_type: str = field(default_factory=str)
    api_region: str = field(default_factory=str)
    api_model: str = field(default_factory=str)
    client: object = field(default_factory=object)
    google_cloud_project_id: str = field(default_factory=str)
    has_formats: bool = field(default_factory=bool)
    development: bool = False
    
    has_dupes: bool = False
    dupe_index_map: list = field(default_factory=list)
    
    request_name: str = field(default_factory=str)
    requests: list = field(default_factory=list)
    requests_uploaded: bool = False
    retries: list = field(default_factory=list)
    request_file_name: str = field(default_factory=str)
    request_file_name_gz: str = field(default_factory=str)
    result_file_name: str = field(default_factory=str)
    error_file_name: str = field(default_factory=str)
    gc_bucket: str = field(default_factory=str)
    gc_input_uri: str = field(default_factory=str)
    gc_output_uri: str = field(default_factory=str)
    
    request_object: object = field(default_factory=object)
    request_file_id: str = field(default_factory=str)
    batch_object: object = field(default_factory=object)
    batch_job_id: str = field(default_factory=str)
    
    status: str = field(default_factory=str)
    status_string: str = field(default_factory=str)
    finished: bool = False
    success: bool = False    
    has_errors: bool = False
    processed: bool = False
    
    status_tracker: APIStatus = field(default_factory=APIStatus)
    results: list = field(default_factory=list)


    def __post_init__(self):
        """ Auto initialize API client and file names """
        
        if self.api_type == "openai":
            if self.development == False:
                self.client = OpenAI(api_key=os.environ['OPENAI_CLIENT_KEY'], max_retries=5)
            else:
                self.client = OpenAI(api_key=os.environ['OPENAI_DEV_KEY'], max_retries=5)
        elif self.api_type in ("vertex", "meta_vertex"):
            self.client = genai.Client(
                vertexai=True,
                project="psb-vertex-testing",
                location=self.api_region,
            )
        
        self.request_object = None
        self.batch_object = None
        
        # remove any special characters from description
        # currently only keeping word characters (letters, digits, underscores) and hyphens
        self.description = re.sub(r'[^\w\-]', '', self.description)
        
        self.request_name = f"{self.description}_batch_{self.id}"
        self.request_file_name = f"{self.file_name}_{self.api_type}_batch_{self.id}_requests.jsonl"
        self.request_file_name_gz = self.request_file_name + ".gz"
        self.result_file_name = f"{self.file_name}_{self.api_type}_batch_{self.id}_results.jsonl.gz"
        self.error_file_name = f"{self.file_name}_{self.api_type}_batch_{self.id}_errors.jsonl.gz"
        
        # Google Cloud fields
        self.gc_bucket = "batches_bucket"
        self.gc_input_uri = f"{self.request_name}_{self.file_name}/input"
        self.gc_output_uri = f"{self.request_name}_{self.file_name}/output"
        
        self.status = "Unstarted"
        self.status_string = f"{self.name} ({self.api_model})- "
        self.status_tracker = APIStatus(
            model=self.api_model,
            set_name=self.name,
        )
        self.retries = []
    
    
    def close(self):
        try:
            if isinstance(self.client, (OpenAI, genai.client.Client)):
                self.client.close()
        except Exception:
            logger.warning("\n***WARNING*** APIBatch.close() - Failed to close client object")
    
    
    def __exit__(self, exc_type, exc, tb):
        self.close()
    
    
    def dedupe_prompts(
            self,
            prompts: list[dict],
            response_formats: list[dict],
            api_file_parts: list[dict],
            silent: bool = False,
    ) -> tuple[list[dict], list[dict], list[dict]]:
        
        self.has_dupes = False
        self.dupe_index_map = []
        self.dedupe_original_index = [] # For each deduped request, the original prompt index it came from

        prompts_deduped = []
        response_formats_deduped = []
        api_file_parts_deduped = []
        seen_prompt_idx = {}
        
        for i in range(len(prompts)):
            p = prompts[i]
            rf = response_formats[i]
            afp = api_file_parts[i]
            prf = []
            if p is not None: prf = p
            if rf is not None: prf = prf + [rf]
            if afp is not None: prf = prf + [afp]
            prompt_key = json.dumps(prf, sort_keys=True)
            if prompt_key not in seen_prompt_idx:
                seen_prompt_idx[prompt_key] = len(prompts_deduped)
                self.dedupe_original_index.append(i)
                prompts_deduped.append(p)
                response_formats_deduped.append(rf)
                api_file_parts_deduped.append(afp)
            self.dupe_index_map.append(seen_prompt_idx[prompt_key])
        
        if len(prompts_deduped) < len(prompts):
                self.has_dupes = True
                if not silent:
                    logger.info((
                        f"Batch '{self.name}':"
                        f" Duplicates detected, magically reducing request count from {len(prompts)}"
                        f" to {len(prompts_deduped)}, just for you :)."
                    ))
                    print((
                        f"Batch '{self.name}':"
                        f" Duplicates detected, from n={len(prompts)}"
                        f" to n={len(prompts_deduped)}"
                    ))
        
        return prompts_deduped, response_formats_deduped, api_file_parts_deduped
    
    
    def build_requests(
            self,
            prompts: list[dict],
            model: str,
            thinking: bool,
            reasoning: str,
            temp: float = None,
            top_p: float = None,
            max_output_tokens: int = None,
            response_formats: list[dict] = None,
            api_file_parts: list[dict] = None,
            silent: bool = False,
    ):
        
        # First dedupe prompts + response_formats
        prompts_deduped, response_formats_deduped, api_file_parts_deduped = self.dedupe_prompts(
            prompts=prompts,
            response_formats=response_formats,
            api_file_parts=api_file_parts,
            silent=silent,
        )
        
        self.status_tracker.num_tasks = len(prompts_deduped)
        self.results = [None] * self.status_tracker.num_tasks
        self.requests = []
        
        match self.api_type:
            case "openai":
                self.build_openai_requests(
                    prompts=prompts_deduped,
                    model=model,
                    thinking=thinking,
                    reasoning=reasoning,
                    temp=temp,
                    top_p=top_p,
                    max_output_tokens=max_output_tokens,
                    response_formats=response_formats_deduped,
                    api_file_parts=api_file_parts_deduped,
                )
            case "vertex":
                self.build_vertex_requests(
                    prompts=prompts_deduped,
                    model=model,
                    thinking=thinking,
                    reasoning=reasoning,
                    temp=temp,
                    top_p=top_p,
                    max_output_tokens=max_output_tokens,
                    response_formats=response_formats_deduped,
                    api_file_parts=api_file_parts_deduped,
                )
            case "meta_vertex":
                self.build_meta_vertex_requests(
                    prompts=prompts_deduped,
                    model=model,
                    thinking=thinking,
                    reasoning=reasoning,
                    temp=temp,
                    top_p=top_p,
                    max_output_tokens=max_output_tokens,
                    response_formats=response_formats_deduped,
                    api_file_parts=api_file_parts_deduped,
                )
    
    
    def build_openai_requests(
            self,
            prompts: list[dict],
            model: str,
            thinking: bool,
            reasoning: str = None,
            temp: float = None,
            top_p: float = None,
            max_output_tokens: int = None,
            response_formats: list[dict] = None,
            api_file_parts: list[dict] = None,
    ):
        
        # Build arguments
        kwargs = {
            "model": model,
        }
        if thinking == True:
            kwargs['reasoning'] = { "effort": reasoning }
        else:
            if top_p is not None:
                kwargs['top_p'] = top_p
            if temp is not None:
                kwargs['temperature'] = temp
        if max_output_tokens is not None:
            #if max_output_tokens < self.max_output_tokens:
            kwargs['max_output_tokens'] = max_output_tokens
        if response_formats is None:
            response_formats = [None]
        if api_file_parts is None:
            api_file_parts = [None]
        
        # Loop over prompts and build request for each
        for prompt_i in range(len(prompts)):
            
            if prompts[prompt_i] is not None:
                
                # If there is structured formatting
                current_format = {}
                if self.has_formats:
                    current_format = { "text": { "format": response_formats[prompt_i]['openai'] } }
                
                # Split up the messages by user/developer
                user_prompt = ""
                developer_prompt = ""
                for d in prompts[prompt_i]:
                    if d.get('role') == 'user':
                        user_prompt = d.get('content')
                    elif d.get('role') == 'developer':
                        developer_prompt = d.get('content')
                
                # If there are file parts
                store_parts = []
                file_parts = []
                if api_file_parts[prompt_i] is not None and 'openai' in api_file_parts[prompt_i]:
                    store_parts = api_file_parts[prompt_i]['openai']['store_parts']
                    file_parts = api_file_parts[prompt_i]['openai']['file_parts']
                
                # Inputs
                current_input = {
                    "input": [
                        { "role": "developer", "content": developer_prompt },
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": user_prompt }
                            ] + file_parts,
                        },
                    ]
                }
                
                # Tools
                current_tools = { "tools": store_parts }
                
                # Save request
                self.requests.append({
                    "custom_id": f"{prompt_i}",
                    "method": "POST",
                    "url": "/v1/responses",
                    "body": kwargs | current_input | current_format | current_tools
                })
    
    
    def build_vertex_requests(
            self,
            prompts: list[dict],
            model: str,
            thinking: bool,
            reasoning: str,
            temp: float = None,
            top_p: float = None,
            max_output_tokens: int = None,
            response_formats: list[dict] = None,
            api_file_parts: list[dict] = None,
    ):
        
        # Build 'generation_config' arguments
        generation_config = { }
        if thinking == True and reasoning is not None:
            generation_config['thinking_config'] = genai.types.ThinkingConfig(
                thinking_budget=int(reasoning)
            ).model_dump(exclude_none=True)
        else:
            if top_p is not None:
                generation_config['topP'] = top_p
            if temp is not None:
                generation_config['temperature'] = temp
        if max_output_tokens is not None:
            #if max_output_tokens < self.max_output_tokens:
            generation_config['max_output_tokens'] = max_output_tokens
        if response_formats is None:
            response_formats = [None]
        if api_file_parts is None:
            api_file_parts = [None]
        #generation_config['media_resolution'] = genai.types.MediaResolution.MEDIA_RESOLUTION_LOW
        
        # Loop over prompts and build request for each
        for prompt_i in range(len(prompts)):
            
            if prompts[prompt_i] is not None:
            
                # Since this is Gemini, we need to split up the messages by user/developer
                user_prompt = ""
                developer_prompt = ""
                for d in prompts[prompt_i]:
                    if d.get('role') == 'user':
                        user_prompt = d.get('content')
                    elif d.get('role') == 'developer':
                        developer_prompt = d.get('content')
                
                # If there is structured formatting
                current_format = {}
                if self.has_formats:
                    current_format = {
                        "response_mime_type": "application/json",
                        #"response_json_schema": response_formats[prompt_i]['vertex'],
                        "response_schema": response_formats[prompt_i]['vertex'],
                    }
                
                # If there are file parts
                current_file_parts = []
                if api_file_parts[prompt_i] is not None and 'vertex' in api_file_parts[prompt_i]:
                    current_file_parts = api_file_parts[prompt_i]['vertex']
                
                # User contents
                current_contents = [
                    genai.types.Content(
                        role="user",
                        parts=[genai.types.Part.from_text(text=user_prompt)] + current_file_parts,
                    ).model_dump(exclude_none=True)
                ]
                
                # Save request
                self.requests.append({
                    "custom_id": f"{prompt_i}",
                    "request": {
                        "contents": current_contents,
                        "system_instruction": { "parts": [ { "text": developer_prompt } ] },
                        "generation_config": generation_config | current_format,
                        "safety_settings": [
                            genai.types.SafetySetting(
                                category=genai.types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                                threshold=genai.types.HarmBlockThreshold.BLOCK_NONE,
                            ).model_dump(exclude_none=True),
                            genai.types.SafetySetting(
                                category=genai.types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                                threshold=genai.types.HarmBlockThreshold.BLOCK_NONE,
                            ).model_dump(exclude_none=True),
                            genai.types.SafetySetting(
                                category=genai.types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                                threshold=genai.types.HarmBlockThreshold.BLOCK_NONE,
                            ).model_dump(exclude_none=True),
                            genai.types.SafetySetting(
                                category=genai.types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                                threshold=genai.types.HarmBlockThreshold.BLOCK_NONE,
                            ).model_dump(exclude_none=True),
                            genai.types.SafetySetting(
                                category=genai.types.HarmCategory.HARM_CATEGORY_CIVIC_INTEGRITY,
                                threshold=genai.types.HarmBlockThreshold.BLOCK_NONE,
                            ).model_dump(exclude_none=True),
                        ]
                    }
                })
    
    
    def build_meta_vertex_requests(
            self,
            prompts: list[dict],
            model: str,
            thinking: bool,
            reasoning: str,
            temp: float = None,
            top_p: float = None,
            max_output_tokens: int = None,
            response_formats: list[dict] = None,
            api_file_parts: list[dict] = None,
    ):

        # Shared body fields (same for every request in the batch).
        # NOTE: "model" here is ignored by Vertex (it's set on the job in start()),
        # but we include it to keep the JSONL valid OpenAI schema.
        shared_body = {
            "model": model,
        }
        if thinking == True:
            shared_body['reasoning'] = { "effort": reasoning }
        else:
            if top_p is not None:
                shared_body['top_p'] = top_p
            if temp is not None:
                shared_body['temperature'] = temp
        if max_output_tokens is not None:
            shared_body['max_tokens'] = max_output_tokens

        if response_formats is None:
            response_formats = [None]
        if api_file_parts is None:
            api_file_parts = [None]

        # Loop over prompts and build a request for each
        for prompt_i in range(len(prompts)):

            if prompts[prompt_i] is not None:

                # Use the flat Responses-style json_schema stored under ['openai']
                # and RE-NEST it under "json_schema" so that it works for Chat Completions style
                current_format = {}
                if self.has_formats:
                    rf = None
                    if response_formats[prompt_i] is not None:
                        rf = response_formats[prompt_i].get('openai')
                    if rf is not None:
                        if rf.get("type") == "json_schema":
                            inner = {k: v for k, v in rf.items() if k != "type"}
                            # don't double-wrap if it's already nested
                            if "json_schema" in inner and set(inner.keys()) == {"json_schema"}:
                                current_format = { "response_format": rf }
                            else:
                                current_format = { "response_format": {"type": "json_schema", "json_schema": inner} }
                        else:
                            # json_object / text pass through unchanged
                            current_format = { "response_format": rf }

                # Split up the messages by user/developer.
                # Chat Completions uses role "system" (not "developer").
                user_prompt = ""
                developer_prompt = ""
                for d in prompts[prompt_i]:
                    if d.get('role') == 'user':
                        user_prompt = d.get('content')
                    elif d.get('role') == 'developer':
                        developer_prompt = d.get('content')

                # File/image parts.
                # These must already be Chat-Completions content blocks, e.g.
                #   {"type": "image_url", "image_url": {"url": "gs://..."}}
                # The Responses-style blocks under ['openai'] (input_text/
                # input_image/input_file) and any file-search tools do NOT carry
                # over, so we only pick up a dedicated ['meta'] entry if present.
                file_parts = []
                if api_file_parts[prompt_i] is not None and 'meta' in api_file_parts[prompt_i]:
                    file_parts = api_file_parts[prompt_i]['meta']

                # Build messages
                messages = []
                if developer_prompt:
                    messages.append({ "role": "system", "content": developer_prompt })
                if file_parts:
                    messages.append({
                        "role": "user",
                        "content": [{ "type": "text", "text": user_prompt }] + file_parts,
                    })
                else:
                    messages.append({ "role": "user", "content": user_prompt })

                # Save request (OpenAI Chat-Completions JSONL line)
                self.requests.append({
                    "custom_id": f"{prompt_i}",
                    "method": "POST",
                    "url": "/v1/chat/completions",
                    "body": shared_body | { "messages": messages } | current_format,
                })
    
    
    def save_requests_to_file(
            self,
            directory: pathlib.Path,
    ):
        # Create normal .jsonl version
        with open(pathlib.Path(directory, self.request_file_name), 'w', encoding='utf-8') as file:
            for req in self.requests:
                file.write(json.dumps(req) + '\n')
        
        # Create compressed .jsonl.gz version
        with gzip.open(pathlib.Path(directory, self.request_file_name_gz), 'wt', encoding='utf-8') as file:
            for req in self.requests:
                file.write(json.dumps(req) + '\n')

    
    def upload_requests(
            self,
            directory: pathlib.Path,
    ):
        match self.api_type:
            
            case "openai":
                
                max_attempts = 5
                for attempt in range(1, max_attempts + 1):
                    try:
                        self.request_object = self.client.files.create(
                            file=pathlib.Path(directory, self.request_file_name),
                            purpose="batch"
                        )
                        self.request_file_id = self.request_object.id
                        self.requests_uploaded = True
                        break
                    except Exception as e:
                        
                        # If max attempts, stop trying
                        if attempt == max_attempts:
                            logger.error(f"\n***ERROR*** OpenAI - Uploading file '{self.request_file_name}' failed with exception: {e}")
                            print(f"\n***ERROR*** OpenAI - Uploading file '{self.request_file_name}' failed with exception: {e}")
                            sys.exit()
                        
                        # Wait for next attempt
                        logger.warning(f"\n***WARNING*** OpenAI - Uploading file '{self.request_file_name}' (attempt={attempt}) failed with exception: {e}")
                        #print(f"\n***WARNING*** OpenAI - Uploading file '{self.request_file_name}' (attempt={attempt}) failed with exception: {e}")
                        sleep = min(60.0, 5.0 * attempt)
                        time.sleep(sleep)
                
            case "vertex" | "meta_vertex":
                
                max_attempts = 5
                for attempt in range(1, max_attempts + 1):                    
                    try:
                        google_storage.Client(project=self.google_cloud_project_id).bucket(self.gc_bucket)\
                              .blob(f"{self.gc_input_uri}/{self.request_file_name}")\
                              .upload_from_filename(pathlib.Path(directory, self.request_file_name))
                        self.requests_uploaded = True
                        break
                    except Exception as e:
                        
                        # If max attempts, stop trying
                        if attempt == max_attempts:
                            logger.error(f"\n***ERROR*** Vertex - Uploading file '{self.request_file_name}' failed with exception: {e}")
                            print(f"\n***ERROR*** Vertex - Uploading file '{self.request_file_name}' failed with exception: {e}")
                            sys.exit()
                        
                        # Wait for next attempt
                        logger.warning(f"\n***WARNING*** Vertex - Uploading file '{self.request_file_name}' failed with exception: {e}")
                        #print(f"\n***WARNING*** Vertex - Uploading file '{self.request_file_name}' failed with exception: {e}")
                        sleep = min(60.0, 5.0 * attempt)
                        time.sleep(sleep)
        
        # Wait 15 seconds, just to be extra sure the requests are fully uploaded and ready
        time.sleep(15)
        
        # Delete uncompressed version of request file (.jsonl), leaving only compressed version (.jsonl.gz) on disk
        self.delete_local_request_file(directory=directory)
        
    
    def delete_local_request_file(
            self,
            directory: pathlib.Path,
    ):
        """
        Delete the uncompressed request file (.jsonl), leaving only the compressed
        version (.jsonl.gz) on disk. Another process (antivirus/indexer/backup) can
        briefly hold a lock on the freshly-written file, so retry a few times - and
        if it stays locked, leave the file behind rather than crash the run.
        """
        request_path = pathlib.Path(directory, self.request_file_name)
        max_attempts = 5
        for attempt in range(1, max_attempts + 1):
            try:
                request_path.unlink(missing_ok=True)
                break
            except OSError as e:

                # If max attempts, give up and leave the file on disk
                if attempt == max_attempts:
                    logger.warning(f"\n***WARNING*** Could not delete uncompressed request file '{self.request_file_name}'"
                                   f" (locked by another process). Leaving it on disk: {e}")
                    break

                # Wait for next attempt
                time.sleep(5.0 * attempt)
                
    
    def start(
            self,
    ):
        # If nothing was uploaded, then nothing to run
        if self.requests_uploaded == False:
            self.finished = True
            return

        max_attempts = 5
        for attempt in range(1, max_attempts + 1):
            try:

                match self.api_type:

                    case "openai":
                        self.batch_object = self.client.batches.create(
                            input_file_id=self.request_file_id,
                            endpoint="/v1/responses",
                            completion_window="24h",
                            metadata={ "description": f"{self.description} - {self.name}" }
                        )
                        self.batch_job_id = self.batch_object.id

                    case "vertex" | "meta_vertex":
                        self.batch_object = self.client.batches.create(
                            model=self.api_model,
                            src=f"gs://{self.gc_bucket}/{self.gc_input_uri}/{self.request_file_name}",
                            config=genai.types.CreateBatchJobConfig(
                                display_name=f"{self.request_name}_{self.file_name}",
                                dest=f"gs://{self.gc_bucket}/{self.gc_output_uri}",
                            ),
                        )

                self.status = "Started"
                return

            except Exception as e:

                # If Rate-limited submission error, then attempt retry
                e_str = str(e).upper()
                rate_limited = ("429" in e_str) or ("RESOURCE_EXHAUSTED" in e_str) or ("RATE LIMIT" in e_str)
                if rate_limited and attempt < max_attempts:
                    delay = min(120.0, 60.0 * attempt)
                    logger.warning(f"\n***WARNING*** Batch job '{self.name}' submission was rate-limited"
                                   f" (attempt={attempt}), waiting {delay:.0f}s before retrying:\n{e}\n")
                    time.sleep(delay)
                    continue

                # Else, mark this batch as failed so the rest of the run carries on without it.
                logger.error(f"\n***ERROR*** Batch job '{self.name}' ({self.api_type}) failed to start"
                             f" - its requests will re-run in normal mode:\n{e}\n")
                # print(f"\n***ERROR*** Batch job '{self.name}' ({self.api_type}) failed to start"
                #       f" - its requests will re-run in normal mode:\n{e}\n")
                self.finished = True
                self.success = False
                self.status = "failed to start"
                self.status_string = (
                    f"{self.name} ({self.api_model}) - "
                    f"{colorama.Fore.RED}{colorama.Style.BRIGHT}FAILED TO START{colorama.Style.RESET_ALL}"
                    f" (requests will re-run in normal mode)"
                )
                self.retries = [int(r['custom_id']) for r in self.requests]
                return
            
    
    def update_status(
            self,
    ):
        match self.api_type:
            
            case "openai":
                try:
                    self.batch_object = self.client.batches.retrieve(self.batch_job_id)
                    self.status = self.batch_object.status
                    if (self.batch_object.request_counts.completed < self.batch_object.request_counts.total or
                        self.batch_object.request_counts.total == 0):
                        self.status_string = (
                            f"{self.name} ({self.api_model}) - "
                            f"{self.batch_object.request_counts.completed} of {self.batch_object.request_counts.total}"
                            f" ({self.batch_object.request_counts.failed} errored)"
                        )
                    else:
                        self.status_string = (
                            f"{self.name} ({self.api_model}) - "
                            f"{colorama.Fore.GREEN}{colorama.Style.BRIGHT}"
                            f"{self.batch_object.request_counts.completed} of {self.batch_object.request_counts.total}"
                            f"{colorama.Style.RESET_ALL}"
                            f" ({self.batch_object.request_counts.failed} errored)"
                        )
                    if self.status in ['failed','completed','expired','cancelled']:
                        self.finished = True
                    if self.status in ['completed','expired','cancelled']:
                        self.success = True
                        if self.batch_object.request_counts.failed > 0:
                            self.has_errors = True
                except Exception as e:
                    logger.error(f"***WARNING*** OpenAI - Updating status for batch job '{self.name}' failed with exception:\n{e}\n")
                
            case "vertex" | "meta_vertex":
                try:
                    genai_terminal_states = {
                        genai.types.JobState.JOB_STATE_SUCCEEDED,
                        genai.types.JobState.JOB_STATE_FAILED,
                        genai.types.JobState.JOB_STATE_CANCELLED,
                        genai.types.JobState.JOB_STATE_EXPIRED,
                        #genai.types.JobState.JOB_STATE_PAUSED,
                    }
                    self.batch_object = self.client.batches.get(name=self.batch_object.name)
                    self.status = self.batch_object.state.name
                    completion_stats = getattr(self.batch_object, 'completion_stats', None)
                    if completion_stats and self.api_type == "vertex":
                        successful_count = self.batch_object.completion_stats.successful_count or 0
                        incomplete_count = self.batch_object.completion_stats.incomplete_count or 0
                        failed_count = self.batch_object.completion_stats.failed_count or 0
                        total_count = successful_count + incomplete_count + failed_count
                        self.status_string = (
                            f"{self.name} ({self.api_model}) - "
                            f"{successful_count} of {total_count}"
                            f" ({failed_count} errored)"
                        )
                    else:
                        self.status_string = (
                            f"{self.name} ({self.api_model}) - {self.status}"
                        )
                    if self.batch_object.state in genai_terminal_states:
                        self.finished = True
                    if self.batch_object.state == genai.types.JobState.JOB_STATE_SUCCEEDED:
                        self.success = True
                        if completion_stats and self.api_type == "vertex":
                            self.status_string = (
                                f"{self.name} ({self.api_model}) - "
                                f"{colorama.Fore.GREEN}{colorama.Style.BRIGHT}"
                                f"{successful_count} of {total_count}"
                                f"{colorama.Style.RESET_ALL}"
                                f" ({failed_count} errored)"
                            )
                        else:
                            self.status_string = (
                                f"{self.name} ({self.api_model}) - "
                                f"{colorama.Fore.GREEN}{colorama.Style.BRIGHT}{self.status}{colorama.Style.RESET_ALL}"
                            )
                except Exception as e:
                    logger.error(f"***WARNING*** Vertex - Updating status for batch job '{self.name}' failed with exception:\n{e}\n")
        
    
    def download_results(
            self,
            directory: pathlib.Path,
    ):
        
        # If finished, download result files
        if self.finished == True:
            
            max_attempts = 5
            for attempt in range(1, max_attempts + 1):
                try:
                    
                    match self.api_type:
                        
                        case "openai":
                            self.batch_object = self.client.batches.retrieve(self.batch_job_id)
                            if self.batch_object.output_file_id is not None:
                                openai_results_file_object = self.client.files.content(self.batch_object.output_file_id)
                                with gzip.open(pathlib.Path(directory, self.result_file_name), 'wb') as file:
                                    file.write(openai_results_file_object.content)
                            if self.batch_object.error_file_id is not None:
                                openai_errors_file_object = self.client.files.content(self.batch_object.error_file_id)
                                with gzip.open(pathlib.Path(directory, self.error_file_name), 'wb') as file:
                                    file.write(openai_errors_file_object.content)
                            if self.request_object is not None:
                                with open(pathlib.Path(directory, f"{self.file_name}_{self.api_type}_batch_{self.id}_request_object.json"), 'w', encoding='utf-8') as file:
                                    json.dump(self.request_object.model_dump(), file, indent=4)
                            if self.batch_object is not None:
                                with open(pathlib.Path(directory, f"{self.file_name}_{self.api_type}_batch_{self.id}_batch_object.json"), 'w', encoding='utf-8') as file:
                                    json.dump(self.batch_object.model_dump(), file, indent=4)
                            
                        # case "vertex" | "meta_vertex":
                        #     self.batch_object = self.client.batches.get(name=self.batch_object.name)
                        #     if self.batch_object.state == genai.types.JobState.JOB_STATE_SUCCEEDED:
                        #         output_bucket_uri, output_prefix = self.batch_object.dest.gcs_uri.replace("gs://", "").split("/", 1)
                        #         for blob in google_storage.Client(project=self.google_cloud_project_id).list_blobs(output_bucket_uri, prefix=output_prefix):
                        #             if "predictions.jsonl" in blob.name:
                        #                 with gzip.open(pathlib.Path(directory, self.result_file_name), 'wb') as f:
                        #                     blob.download_to_file(f)
                        #     if self.batch_object is not None:
                        #         with open(pathlib.Path(directory, f"{self.file_name}_{self.api_type}_batch_{self.id}_batch_object.json"), 'w', encoding='utf-8') as file:
                        #             json.dump(self.batch_object.to_json_dict(), file, indent=4)
                        
                        case "vertex" | "meta_vertex":
                            self.batch_object = self.client.batches.get(name=self.batch_object.name)
                            if self.batch_object.state == genai.types.JobState.JOB_STATE_SUCCEEDED:
                        
                                # Determine correct Google Cloud output directory
                                output_dir_uri = None
                                dest = getattr(self.batch_object, "dest", None)
                                if dest is not None:
                                    output_dir_uri = getattr(dest, "gcs_uri", None)
                                if not output_dir_uri:
                                    # fallback to the prefix we asked for
                                    output_dir_uri = f"gs://{self.gc_bucket}/{self.gc_output_uri}"
                                output_bucket_uri, output_prefix = output_dir_uri.replace("gs://", "").split("/", 1)
                        
                                # Collect prediction shards: any .jsonl that is NOT an error file.
                                client = google_storage.Client(project=self.google_cloud_project_id)
                                pred_blobs = []
                                for blob in client.list_blobs(output_bucket_uri, prefix=output_prefix):
                                    name = blob.name.lower()
                                    if name.endswith(".jsonl") and "error" not in name.rsplit("/", 1)[-1]:
                                        pred_blobs.append(blob)
                        
                                if not pred_blobs:
                                    logger.error(
                                        f"\n***ERROR*** Vertex - No prediction .jsonl files found under "
                                        f"gs://{output_bucket_uri}/{output_prefix}"
                                    )
                                else:
                                    # Sort for deterministic ordering across shards
                                    pred_blobs.sort(key=lambda b: b.name)
                                    # Concatenate all shards into the single gzipped results file
                                    with gzip.open(pathlib.Path(directory, self.result_file_name), 'wb') as out_f:
                                        for blob in pred_blobs:
                                            blob.download_to_file(out_f)
                                    logger.info(
                                        f"File='{self.file_name}' downloaded {len(pred_blobs)} prediction "
                                        f"shard(s) from gs://{output_bucket_uri}/{output_prefix}"
                                    )
                        
                            if self.batch_object is not None:
                                with open(pathlib.Path(directory, f"{self.file_name}_{self.api_type}_batch_{self.id}_batch_object.json"), 'w', encoding='utf-8') as file:
                                    json.dump(self.batch_object.to_json_dict(), file, indent=4)
                                    
                    break
                
                except Exception as e:
                        
                        # If max attempts, stop trying
                        if attempt == max_attempts:
                            logger.error(f"\n***ERROR*** Downloading results '{self.result_file_name}' failed with exception: {e}")
                            print(f"\n***ERROR*** Downloading results '{self.result_file_name}' failed with exception: {e}")
                        
                        # Wait for next attempt
                        else:
                            logger.warning(f"\n***WARNING*** Downloading results '{self.result_file_name}' (attempt={attempt}) failed with exception: {e}")
                            sleep = min(60.0, 5.0 * attempt)
                            time.sleep(sleep)


    def process_results(
            self,
            directory: pathlib.Path,
    ):
        self.retries = []
        
        # If finished, download result files
        if self.finished == True:
            
            match self.api_type:

                case "openai":
                    # If the batch job itself failed, log the reasons (results will be empty and all IDs re-tried)
                    if self.status == "failed":
                        batch_errors = getattr(self.batch_object, "errors", None)
                        error_data = getattr(batch_errors, "data", None) if batch_errors is not None else None
                        for err in error_data or []:
                            err_msg = getattr(err, "message", str(err))
                            logger.error(f"OpenAI - Batch job '{self.name}' FAILED: {err_msg}")
                            print(f"\n***ERROR*** OpenAI - Batch job '{self.name}' FAILED: {err_msg}")
                    self.process_openai_results(directory=directory)
                    self.process_openai_failures(directory=directory)

                case "vertex":
                    self.process_vertex_results(directory=directory)

                case "meta_vertex":
                    self.process_meta_vertex_results(directory=directory)

        # Remove duplicate retry IDs (an ID can be flagged by more than one check)
        self.retries = sorted(set(self.retries))

        # Mark as 'processed'
        self.processed = True


    def process_openai_results(
            self,
            directory: pathlib.Path,
    ):
        
        # Open results file and convert each to into OpenAI Response object
        # (a wholly failed batch has no output file - skip reading so every ID falls into the retry list below)
        id_list = []
        result_list = []
        if self.batch_object is not None and self.batch_object.output_file_id is not None:

            ###########################################################################################
            # Added checks for 'prompt_cache_retention' field
            ###########################################################################################

            # Determine what values the current openai package expects for prompt_cache_retention
            _allowed_retention_values = set()
            try:
                schema = openai_response.model_json_schema()
                # Walk the schema to find the prompt_cache_retention field's allowed values
                props = schema.get("properties", {})
                field = props.get("prompt_cache_retention", {})
                # Could be directly on the field, or behind an anyOf/oneOf, or in $defs
                if "enum" in field:
                    _allowed_retention_values = set(field["enum"])
                else:
                    for sub in field.get("anyOf", field.get("oneOf", [])):
                        if "enum" in sub:
                            _allowed_retention_values.update(sub["enum"])
                        elif "$ref" in sub:
                            ref_name = sub["$ref"].split("/")[-1]
                            ref_schema = schema.get("$defs", {}).get(ref_name, {})
                            if "enum" in ref_schema:
                                _allowed_retention_values.update(ref_schema["enum"])
            except Exception:
                _allowed_retention_values = set()

            # Build a normalizer: map both variants to whichever one the model actually accepts
            _retention_normalize = {}
            for val in _allowed_retention_values:
                _retention_normalize[val] = val
                # Map the "other" variant to this one
                if "-" in val:
                    _retention_normalize[val.replace("-", "_")] = val
                elif "_" in val:
                    _retention_normalize[val.replace("_", "-")] = val

            ###########################################################################################

            try:
                #with open(pathlib.Path(directory, self.result_file_name), 'r', encoding='utf-8') as file:
                with gzip.open(pathlib.Path(directory, self.result_file_name), 'rt', encoding='utf-8') as file:
                    for result_line in file:
                        result_json = json.loads(result_line.strip())
                        result_body = result_json['response']['body']

                        # Checks 'prompt_cache_retention' field
                        raw = result_body.get('prompt_cache_retention')
                        if raw and raw in _retention_normalize:
                            result_body['prompt_cache_retention'] = _retention_normalize[raw]

                        resp: openai_response = openai_response.model_validate(result_body)
                        id_list.append(int(result_json['custom_id']))
                        result_list.append(resp)
            except Exception as e:
                logger.error(f"\n***ERROR*** OpenAI - Processing results '{self.result_file_name}' failed with exception: {e}")
                print(f"\n***ERROR*** OpenAI - Processing results '{self.result_file_name}' failed with exception: {e}")

        # Check for missing IDs that need to be re-tried
        returned_ids = set(id_list)
        for r in self.requests:
            r_id = int(r['custom_id'])
            if r_id not in returned_ids:
                self.retries.append(r_id)

        # Process results
        for i in range(len(result_list)):
            
            error = None
            
            api_results = result_list[i]
            
            # If needed, check for proper JSON formatting
            if self.has_formats == True:
                try:
                    response_text = APIRequest.fix_json_string(api_results.output_text)
                    json.loads(response_text)
                except Exception as e:
                    logger.error(f"File='{self.file_name}' id#{id_list[i]} result has invalid JSON: {e}")
                    error = e
                    self.retries.append(id_list[i])
                
            # Finally, if nothing else, save text response
            else:
                response_text = api_results.output_text
            
            # If no errors, save results to list
            if not error:
                self.results[id_list[i]] = response_text
            
            # Tokens + costs
            if api_results.usage is not None:
                
                # Cached tokens
                cached_tokens_in = 0
                if api_results.usage.input_tokens_details is not None:
                    if api_results.usage.input_tokens_details.cached_tokens is not None:
                            cached_tokens_in = api_results.usage.input_tokens_details.cached_tokens
                
                # Reasoning tokens
                reasoning_tokens_out = 0
                if api_results.usage.output_tokens_details is not None:
                    if api_results.usage.output_tokens_details.reasoning_tokens is not None:
                        reasoning_tokens_out = api_results.usage.output_tokens_details.reasoning_tokens
                
                # Input tokens
                tokens_in = 0
                if api_results.usage.input_tokens is not None:
                        tokens_in = api_results.usage.input_tokens
                
                # Output tokens
                tokens_out = 0
                if api_results.usage.output_tokens is not None:
                        tokens_out = api_results.usage.output_tokens
                
                # Total tokens
                total_tokens = 0
                if api_results.usage.total_tokens is not None:
                        total_tokens = api_results.usage.total_tokens
                
                # Log token usage
                self.status_tracker.tokens_in += tokens_in
                self.status_tracker.cached_tokens_in += cached_tokens_in
                self.status_tracker.tokens_out += tokens_out
                self.status_tracker.reasoning_tokens_out += reasoning_tokens_out
                self.status_tracker.last_request_tokens = total_tokens
                
                # Calculate costs (50% batch discount)
                self.status_tracker.total_cost += 0.5 * self.status_tracker.estimate_costs(
                    model_name=api_results.model,
                    in_t=tokens_in,
                    cin_t=cached_tokens_in,
                    out_t=tokens_out,
                )


    def process_openai_failures(
            self,
            directory: pathlib.Path,
    ):
        
        if self.batch_object is None or self.batch_object.error_file_id is None:
            return
        
        try:
            # Open errors file and add ids to retry list
            with gzip.open(pathlib.Path(directory, self.error_file_name), 'rt', encoding='utf-8') as file:
                for result_line in file:
                    
                    try:
                        result_json = json.loads(result_line.strip())
                        failed_id = int(result_json['custom_id'])
                    except Exception as e:
                        logger.error(f"File='{self.file_name}' In OpenAI errors file, could not extract ID from:\n{result_line}\n{e}")
                        print(f"File='{self.file_name}' In OpenAI errors file, could not extract ID from:\n{result_line}\n{e}")
                    
                    self.retries.append(failed_id)
                    
        except Exception as e:
            logger.error(f"\n***ERROR*** OpenAI - Processing errors '{self.error_file_name}' failed with exception: {e}")
            print(f"\n***ERROR*** OpenAI - Processing errors '{self.error_file_name}' failed with exception: {e}")
            return


    def process_vertex_results(
            self,
            directory: pathlib.Path,
    ):
        
        # Open results file and convert each to into Vertex Response object
        id_list = []
        full_result_list = []
        result_list = []
        try:
            with gzip.open(pathlib.Path(directory, self.result_file_name), 'rt', encoding='utf-8') as file:
                for line_index, result_line in enumerate(file):
                    result_json = json.loads(result_line.strip())
                    
                    # Remove fields that the package model doesn't recognize yet:
                    try:
                        result_json['response']['candidates'][0].pop("score", None)
                    except Exception: 
                        pass
                    try:
                        result_json['response']['usageMetadata'].pop("billablePromptUsage", None)
                    except Exception: 
                        pass
                    
                    resp: genai.types.GenerateContentResponse = genai.types.GenerateContentResponse.model_validate(result_json['response'])
                    
                    cid_raw = result_json.get('custom_id')
                    if cid_raw is not None and cid_raw != "":
                        try:
                            cid = int(cid_raw)
                        except (TypeError, ValueError):
                            logger.warning(f"File='{self.result_file_name}' row {line_index} has non-int custom_id {cid_raw!r}; skipping (will retry).")
                            continue
                        id_list.append(cid)
                        full_result_list.append(result_json)
                        result_list.append(resp)
        
        except Exception as e:
            logger.error(f"\n***ERROR*** Vertex - Processing results '{self.result_file_name}' failed with exception: {e}")
            #print(f"\n***ERROR*** Vertex - Processing results '{self.result_file_name}' failed with exception: {e}")
        
        # Check for missing IDs that need to be re-tried
        returned_ids = set(id_list)
        for r in self.requests:
            r_id = int(r['custom_id'])
            if r_id not in returned_ids:
                self.retries.append(r_id)
        
        # Process results
        for i in range(len(result_list)):
            
            error = None
            
            full_result = full_result_list[i]
            api_results = result_list[i]
            
            # Empty response
            if api_results is None:
                self.retries.append(id_list[i])
                continue

            # Status unusual?
            if full_result['status'] != "":
                error = full_result['status']
                error_dict = json.loads(error)
                logger.error(f"File='{self.file_name}' id#{id_list[i]} result had unusual status: {error}")
                
                # Retry, depending on the status code
                needs_retry = True
                if "code" in error_dict:
                    # Code '9' = Access Denied / Requires valid user credentials in the RPC
                    if error_dict['code'] == 9:
                        needs_retry = False
                        logger.error(f"File='{self.file_name}' id#{id_list[i]} - STATUS CODE = '9' - Access Denied")
                        #self.results[id_list[i]] = f"{{ \"DENIED\": \"{error_dict['message']}\" }}"
                        self.results[id_list[i]] = None
                if needs_retry:
                    self.retries.append(id_list[i])
            
            # If no exception caught, but response was blocked
            elif api_results.prompt_feedback is not None and api_results.prompt_feedback.block_reason is not None:
                error = api_results.prompt_feedback.block_reason
                logger.warning(f"File='{self.file_name}' id#{id_list[i]} WAS BLOCKED FOR UNKNOWN REASON!:\n\n{api_results.prompt_feedback.block_reason}\n\n{api_results}")
                #self.results[id_list[i]] = f"{{ \"BLOCKED\": \"{api_results.prompt_feedback.block_reason.value}\" }}"
                self.results[id_list[i]] = None
            
            # If finished for SAFETY reason
            elif api_results.candidates is not None and api_results.candidates[0].finish_reason is genai.types.FinishReason.SAFETY:
                error = api_results.candidates[0].finish_reason
                logger.warning(f"File='{self.file_name}' id#{id_list[i]} FINISHED FOR SAFTEY REASONS!:\n\n{api_results.candidates[0].finish_reason}\n\n{api_results}")
                #self.results[id_list[i]] = f"{{ \"BLOCKED FOR SAFETY REASONS\": \"{api_results.candidates[0].finish_reason}\" }}"
                self.results[id_list[i]] = None
            
            # Else finished for unknown finish reason
            elif api_results.candidates[0].finish_reason is not genai.types.FinishReason.STOP:
                error = api_results.candidates[0].finish_reason
                logger.warning(f"File='{self.file_name}' id#{id_list[i]} finished with abnormal reason!: {str(api_results.candidates[0].finish_reason)}")
                self.retries.append(id_list[i])
            
            # If needed, check for proper JSON formatting
            if self.has_formats == True:
                try:
                    response_text = APIRequest.fix_json_string(api_results.text)
                    json.loads(response_text)
                except Exception as e:
                    logger.error(f"File='{self.file_name}' id#{id_list[i]} result has invalid JSON: {e}")
                    error = e
                    self.retries.append(id_list[i])
                
            # Finally, if nothing else, save text response
            else:
                response_text = api_results.text
            
            # If no errors, save results to list
            if not error:
                self.results[id_list[i]] = response_text
            
            # Tokens + costs
            if api_results.usage_metadata is not None:
                
                # Cached tokens
                cached_tokens_in = 0
                if api_results.usage_metadata.cached_content_token_count is not None:
                        cached_tokens_in = api_results.usage_metadata.cached_content_token_count
                
                # Reasoning tokens
                reasoning_tokens_out = 0
                if api_results.usage_metadata.thoughts_token_count is not None:
                    reasoning_tokens_out = api_results.usage_metadata.thoughts_token_count
                
                # Input tokens
                tokens_in = 0
                if api_results.usage_metadata.prompt_token_count is not None:
                        tokens_in = api_results.usage_metadata.prompt_token_count
                
                # Output tokens
                tokens_out = 0
                if api_results.usage_metadata.candidates_token_count is not None:
                        tokens_out = api_results.usage_metadata.candidates_token_count
                
                # Total tokens
                total_tokens = 0
                if api_results.usage_metadata.total_token_count is not None:
                        total_tokens = api_results.usage_metadata.total_token_count
                
                # Log token usage
                self.status_tracker.tokens_in += tokens_in
                self.status_tracker.cached_tokens_in += cached_tokens_in
                self.status_tracker.tokens_out += tokens_out + reasoning_tokens_out
                self.status_tracker.reasoning_tokens_out += reasoning_tokens_out
                self.status_tracker.last_request_tokens = total_tokens
                
                # Calculate costs (50% batch discount)
                self.status_tracker.total_cost += 0.5 * self.status_tracker.estimate_costs(
                    model_name=self.api_model,
                    in_t=tokens_in,
                    cin_t=cached_tokens_in,
                    out_t=tokens_out + reasoning_tokens_out,
                )
    
    
    def process_meta_vertex_results(
            self,
            directory: pathlib.Path,
    ):

        # Read the output JSONL. Each row: {status, request, response, custom_id}
        # where "response" is an OpenAI chat-completion dict. We parse the dict
        # directly (no pydantic) to tolerate any extra Vertex-side fields.
        rows = []
        id_list = []
        try:
            with gzip.open(pathlib.Path(directory, self.result_file_name), 'rt', encoding='utf-8') as file:
                for line_index, result_line in enumerate(file):
                    result_json = json.loads(result_line.strip())
                    cid_raw = result_json.get('custom_id')
                    if cid_raw is not None and cid_raw != "":
                        try:
                            cid = int(cid_raw)
                        except (TypeError, ValueError):
                            logger.warning(f"File='{self.result_file_name}' row {line_index} has non-int custom_id {cid_raw!r}; skipping (will retry).")
                            continue
                        rows.append((cid, result_json))
                        id_list.append(cid)

        except Exception as e:
            logger.error(f"\n***ERROR*** Vertex(meta) - Processing results '{self.result_file_name}' failed with exception: {e}")
            #print(f"\n***ERROR*** Vertex(meta) - Processing results '{self.result_file_name}' failed with exception: {e}")
        
        # Check for missing IDs that need to be re-tried
        returned_ids = set(id_list)
        for r in self.requests:
            r_id = int(r['custom_id'])
            if r_id not in returned_ids:
                self.retries.append(r_id)
        
        # Process results
        for cid, full_result in rows:

            error = None

            # Row-level status (non-empty == error from the batch service)
            status = full_result.get('status', "")
            if status not in ("", None):
                error = status
                logger.error(f"File='{self.file_name}' id#{cid} result had unusual status: {status}")

                # Decide whether to retry based on the status code, if present
                needs_retry = True
                try:
                    error_dict = json.loads(status) if isinstance(status, str) else status
                except Exception:
                    error_dict = {}
                if isinstance(error_dict, dict) and "code" in error_dict:
                    # Code '9' = Access Denied / requires valid user credentials
                    if error_dict['code'] == 9:
                        needs_retry = False
                        logger.error(f"File='{self.file_name}' id#{cid} - STATUS CODE = '9' - Access Denied")
                        self.results[cid] = None
                if needs_retry:
                    self.retries.append(cid)

            # Otherwise inspect the chat-completion response
            else:
                response = full_result.get('response') or {}
                choices = response.get('choices') or []

                if not choices:
                    error = "no_choices"
                    logger.warning(f"File='{self.file_name}' id#{cid} response had no choices:\n{response}")
                    self.retries.append(cid)
                else:
                    choice = choices[0]
                    message = choice.get('message') or {}
                    finish_reason = choice.get('finish_reason')
                    refusal = message.get('refusal')
                    content = message.get('content')

                    # Safety filter / refusal (only if Llama Guard is enabled;
                    # Llama 4 MaaS does not run it by default). Hard failure.
                    if finish_reason == "content_filtered" or refusal:
                        error = finish_reason or "refusal"
                        logger.warning(f"File='{self.file_name}' id#{cid} WAS BLOCKED (content_filtered/refusal): {refusal}")
                        self.results[cid] = None

                    # Abnormal finish (anything other than a normal stop/length) -> retry
                    elif finish_reason not in ("stop", "length", None):
                        error = finish_reason
                        logger.warning(f"File='{self.file_name}' id#{cid} finished with abnormal reason!: {finish_reason}")
                        self.retries.append(cid)

                    # Normal completion
                    else:
                        if self.has_formats == True:
                            try:
                                response_text = APIRequest.fix_json_string(content)
                                json.loads(response_text)
                            except Exception as e:
                                logger.error(f"File='{self.file_name}' id#{cid} result has invalid JSON: {e}")
                                error = e
                                self.retries.append(cid)
                        else:
                            response_text = content

                        if not error:
                            self.results[cid] = response_text

            # Tokens + costs (OpenAI usage shape; guarded since errored rows
            # may not include usage at all)
            response = full_result.get('response') or {}
            usage = response.get('usage') or {}
            if usage:

                # Cached tokens (usually absent on Vertex Llama)
                cached_tokens_in = 0
                ptd = usage.get('prompt_tokens_details') or {}
                if isinstance(ptd, dict) and ptd.get('cached_tokens') is not None:
                    cached_tokens_in = ptd['cached_tokens']

                # Reasoning tokens (absent for Llama; present for OpenAI reasoning models)
                reasoning_tokens_out = 0
                ctd = usage.get('completion_tokens_details') or {}
                if isinstance(ctd, dict) and ctd.get('reasoning_tokens') is not None:
                    reasoning_tokens_out = ctd['reasoning_tokens']

                # Input / output / total
                tokens_in = usage.get('prompt_tokens') or 0
                tokens_out = usage.get('completion_tokens') or 0
                total_tokens = usage.get('total_tokens') or 0

                # Log token usage. (completion_tokens already includes any
                # reasoning tokens, so we do NOT add reasoning again here --
                # this matches your OpenAI processor, not the Gemini one.)
                self.status_tracker.tokens_in += tokens_in
                self.status_tracker.cached_tokens_in += cached_tokens_in
                self.status_tracker.tokens_out += tokens_out
                self.status_tracker.reasoning_tokens_out += reasoning_tokens_out
                self.status_tracker.last_request_tokens = total_tokens

                # Calculate costs (50% batch discount)
                self.status_tracker.total_cost += 0.5 * self.status_tracker.estimate_costs(
                    model_name=self.api_model,
                    in_t=tokens_in,
                    cin_t=cached_tokens_in,
                    out_t=tokens_out,
                )
                
    
    def duplicate_results(
            self,
    ):
        if self.has_dupes == True:
            results_deduped = deepcopy(self.results)
            self.results = [results_deduped[item] for item in self.dupe_index_map]


    def delete_server_files(
            self,
    ):
        
        match self.api_type:
            
            case "openai":
                
                # Input
                try:
                    if self.request_file_id is not None:
                        file_deletion = self.client.files.delete(self.request_file_id)
                        if file_deletion.deleted == False:
                            logger.warning(f"\n***WARNING*** Openai batch job '{self.name}': failed to delete INPUT file on server." +
                                           " ID={self.request_file_id}")
                except Exception as e:
                    logger.warning(f"\n***WARNING*** Openai batch job '{self.name}': failed to delete INPUT file on server." +
                                   f" ID={self.request_file_id}:\n{e}\n")
                
                if self.batch_object is not None:
                    
                    # Output
                    try:
                        if self.batch_object.output_file_id is not None:
                            file_deletion = self.client.files.delete(self.batch_object.output_file_id)
                            if file_deletion.deleted == False:
                                logger.warning(f"\n***WARNING*** Openai batch job '{self.name}': failed to delete OUTPUT file on server." +
                                               " ID={self.batch_object.output_file_id}")
                    except Exception as e:
                        logger.warning(f"\n***WARNING*** Openai batch job '{self.name}': failed to delete OUTPUT file on server." +
                                       f" ID={self.batch_object.output_file_id}:\n{e}\n")
                        
                    # Errors
                    try:
                        if self.batch_object.error_file_id is not None:
                            file_deletion = self.client.files.delete(self.batch_object.error_file_id)
                            if file_deletion.deleted == False:
                                logger.warning(f"\n***WARNING*** Openai batch job '{self.name}': failed to delete ERROR file on server." +
                                               " ID={self.batch_object.error_file_id}")
                    except Exception as e:
                        logger.warning(f"\n***WARNING*** Openai batch job '{self.name}': failed to delete ERROR file on server." +
                                       f" ID={self.batch_object.error_file_id}:\n{e}\n")
            
            case "vertex" | "meta_vertex":
                if len(self.requests) > 0:
                    # Storage blobs
                    try:
                        bucket = google_storage.Client(project=self.google_cloud_project_id).bucket(self.gc_bucket)
                        blobs = bucket.list_blobs(prefix=self.request_name)
                        for blob in blobs:
                            generation_match_precondition = None
                            blob.reload()
                            generation_match_precondition = blob.generation
                            blob.delete(if_generation_match=generation_match_precondition)
                        
                    except Exception as e:
                        logger.warning(f"\n***WARNING*** Vertex batch job '{self.name}': failed to delete file blob on server." +
                                       f" NAME={self.request_name}:\n{e}\n")
    
                    # Batch job
                    try:
                        self.client.batches.delete(name=self.batch_object.name)
                    except Exception as e:
                        logger.warning(f"\n***WARNING*** Vertex batch job '{self.name}': failed to delete batch job on server." +
                                       f" NAME={self.batch_object.name}:\n{e}\n")

