from __future__ import annotations

import json
import re
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator


class Auditor(Protocol):
    """Anything that turns a fully rendered prompt into raw model text."""

    def generate(self, prompt: str) -> str: ...


class SecretAuditResult(BaseModel):
    risk_score: int = Field(..., description="0 (safe) to 10 (high risk secrets).")
    regex_matches_found: bool = Field(False, description="Set from layer 1, never from the model.")
    llm_confidence_score: int = Field(0, description="Assessment of sensitive data leakage, 0-10.")
    reason: str = Field("", description="Rationale for the assigned scores.")
    flagged_snippets: list[str] = Field(default_factory=list)

    @field_validator("risk_score", "llm_confidence_score", mode="before")
    @classmethod
    def _clamp(cls, value: Any) -> int:
        if isinstance(value, bool):
            raise ValueError("score must be a number")
        return max(0, min(10, round(float(value))))

    @field_validator("flagged_snippets", mode="before")
    @classmethod
    def _snippets(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("flagged_snippets must be a list")
        return [str(item) for item in value]


def render_prompt(template: str, content: str) -> str:
    # Plain replacement keeps literal braces (e.g. a JSON schema) in prompts safe.
    return template.replace("{content}", content)


_FENCE = re.compile(r"```(?:json)?", re.IGNORECASE)


def parse_audit_response(text: str) -> SecretAuditResult:
    """Extract and validate the first JSON object in model output."""
    cleaned = _FENCE.sub("", text).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in model output")
    data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("model output is not a JSON object")
    return SecretAuditResult.model_validate(data)


def llm_available() -> bool:
    import importlib.util

    return all(importlib.util.find_spec(name) for name in ("torch", "transformers"))


class LocalLLMAuditor:
    """Runs an open-weights model in-process with transformers (no external daemon)."""

    def __init__(self, model_id: str, device: str = "auto", max_new_tokens: int = 256) -> None:
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise ImportError(
                "The LLM layer requires transformers and torch. "
                "Install them with: pip install 'matlock[audit,audit-llm]'"
            ) from exc
        if device == "auto":
            if torch.backends.mps.is_available():
                device = "mps"
            elif torch.cuda.is_available():
                device = "cuda"
            else:
                device = "cpu"
        self._torch = torch
        self.device = device
        self.max_new_tokens = max_new_tokens
        self.tokenizer = AutoTokenizer.from_pretrained(model_id)
        self.model = AutoModelForCausalLM.from_pretrained(
            model_id, dtype=torch.float32 if device == "cpu" else torch.float16
        ).to(device)
        self.model.eval()

    def generate(self, prompt: str) -> str:
        messages = [{"role": "user", "content": prompt}]
        inputs = self.tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, return_tensors="pt", return_dict=True
        ).to(self.device)
        with self._torch.no_grad():
            output = self.model.generate(
                **inputs,
                max_new_tokens=self.max_new_tokens,
                do_sample=False,
                pad_token_id=self.tokenizer.eos_token_id,
            )
        generated = output[0][inputs["input_ids"].shape[-1] :]
        return self.tokenizer.decode(generated, skip_special_tokens=True).strip()
