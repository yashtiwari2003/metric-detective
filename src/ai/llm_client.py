import os
import json
import time
import logging
from pathlib import Path
from typing import Optional, Union, Type, Any
from pydantic import BaseModel
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / '.env')
logger = logging.getLogger(__name__)

total_calls = 0
total_input_tokens = 0
total_output_tokens = 0
total_cost = 0.0

def get_usage_stats() -> dict[str, Any]:
    """Get usage statistics for LLM calls."""
    return {
        "total_calls": total_calls,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_cost": total_cost
    }

def log_llm_call(provider: str, model: str, system: str, user: str, response: str, latency: float, in_tokens: int, out_tokens: int, cost: float, success: bool) -> None:
    """Log an LLM call to JSON lines format."""
    log_file = PROJECT_ROOT / 'logs' / 'llm_calls.jsonl'
    log_file.parent.mkdir(parents=True, exist_ok=True)
    
    log_entry = {
        "timestamp": time.time(),
        "provider": provider,
        "model": model,
        "system_prompt": system[:200],
        "user_prompt": user[:200],
        "response": response[:500],
        "latency_ms": latency,
        "input_tokens": in_tokens,
        "output_tokens": out_tokens,
        "cost_estimate": cost,
        "prompt_version": "1.0.0",
        "success": success
    }
    with open(log_file, 'a') as f:
        f.write(json.dumps(log_entry) + '\n')

def call_llm(system: str, user: str, response_model: Optional[Type[BaseModel]] = None, temperature: float = 0.2) -> Union[str, BaseModel]:
    """Call the configured LLM provider and optionally return a structured Pydantic model."""
    global total_calls, total_input_tokens, total_output_tokens, total_cost
    
    provider = os.getenv('LLM_PROVIDER', 'gemini')
    api_key = os.getenv('LLM_API_KEY')
    model = os.getenv('LLM_MODEL', 'gemini-2.5-flash-lite')
    
    max_retries = 3
    delay = 1
    
    for attempt in range(max_retries):
        start_time = time.time()
        try:
            res_text = ""
            in_tokens = 0
            out_tokens = 0
            cost = 0.0
            
            if provider == 'gemini':
                from google import genai
                from google.genai import types
                
                client = genai.Client(api_key=api_key)
                
                config_kwargs = {"temperature": temperature}
                if response_model:
                    config_kwargs["response_mime_type"] = "application/json"
                    config_kwargs["response_schema"] = response_model.model_json_schema()
                    
                config = types.GenerateContentConfig(**config_kwargs)
                contents = f"System: {system}\n\nUser: {user}"
                
                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config
                )
                res_text = response.text
                
                # Mock token counting for illustration since token counts are not standardly returned in all APIs
                in_tokens = len(contents) // 4
                out_tokens = len(res_text) // 4 if res_text else 0
                cost = (in_tokens * 0.0375 / 1_000_000) + (out_tokens * 0.15 / 1_000_000)

            elif provider == 'openai':
                from openai import OpenAI
                client = OpenAI(api_key=api_key)
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user}
                ]
                kwargs = {"temperature": temperature}
                if response_model:
                    kwargs["response_format"] = {"type": "json_object"}
                    
                completion = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    **kwargs
                )
                res_text = completion.choices[0].message.content or ""
                if completion.usage:
                    in_tokens = completion.usage.prompt_tokens
                    out_tokens = completion.usage.completion_tokens
                cost = 0.0 # Placeholder
                
            elif provider == 'groq':
                from groq import Groq
                client = Groq(api_key=api_key)
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user}
                ]
                kwargs = {"temperature": temperature}
                if response_model:
                    kwargs["response_format"] = {"type": "json_object"}
                    
                completion = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    **kwargs
                )
                res_text = completion.choices[0].message.content or ""
                if completion.usage:
                    in_tokens = completion.usage.prompt_tokens
                    out_tokens = completion.usage.completion_tokens
                cost = 0.0 # Placeholder
                
            else:
                raise ValueError(f"Unknown provider {provider}")
                
            latency = (time.time() - start_time) * 1000
            
            total_calls += 1
            total_input_tokens += in_tokens
            total_output_tokens += out_tokens
            total_cost += cost
            
            log_llm_call(provider, model, system, user, res_text, latency, in_tokens, out_tokens, cost, True)
            
            if response_model:
                return response_model.model_validate_json(res_text)
            return res_text
            
        except Exception as e:
            latency = (time.time() - start_time) * 1000
            log_llm_call(provider, model, system, user, str(e), latency, 0, 0, 0.0, False)
            if attempt == max_retries - 1:
                logger.error(f"LLM call failed after {max_retries} attempts: {e}")
                raise
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("LLM call failed")
