import os
import asyncio

import torch
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from typing import List, Optional


def load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


load_dotenv()
hf_token = os.environ.get("HF_TOKEN") or os.environ.get("hf_token")

MODEL_ID = "google/gemma-2-9b-it"

quantization_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_use_double_quant=True,
    bnb_4bit_compute_dtype=torch.bfloat16,
)

print(f"Loading model {MODEL_ID}...")
tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, token=hf_token)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_ID,
    token=hf_token,
    quantization_config=quantization_config,
    device_map="auto",
)
model.eval()
print("Model loaded.")

app = FastAPI(title="Gemma API")


# ---------- schemas ----------

class Message(BaseModel):
    role: str   # "user" | "assistant" | "system"
    content: str

class ChatRequest(BaseModel):
    messages: List[Message]
    max_new_tokens: Optional[int] = 256
    temperature: Optional[float] = 0.8
    top_p: Optional[float] = 0.95
    stream: Optional[bool] = False

class GenerateRequest(BaseModel):
    prompt: str
    max_new_tokens: Optional[int] = 256
    temperature: Optional[float] = 0.8
    top_p: Optional[float] = 0.95
    stream: Optional[bool] = False


# ---------- helpers ----------

def build_prompt(messages: List[Message]) -> str:
    prompt = ""
    for msg in messages:
        if msg.role in ("user", "system"):
            prompt += f"<start_of_turn>user\n{msg.content}<end_of_turn>\n"
        elif msg.role == "assistant":
            prompt += f"<start_of_turn>model\n{msg.content}<end_of_turn>\n"
    prompt += "<start_of_turn>model\n"
    return prompt


def _generate(prompt: str, max_new_tokens: int, temperature: float, top_p: float) -> str:
    inputs = tokenizer(prompt, return_tensors="pt")
    device = next(model.parameters()).device
    inputs = {k: v.to(device) for k, v in inputs.items()}
    input_len = inputs["input_ids"].shape[1]

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=True,
            temperature=temperature,
            top_p=top_p,
        )

    generated = outputs[0][input_len:]
    return tokenizer.decode(generated, skip_special_tokens=True).strip()


def word_stream(text: str):
    for word in text.split(" "):
        yield word + " "


# ---------- routes ----------

@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_ID}


@app.post("/generate")
async def generate(req: GenerateRequest):
    """Raw text-in / text-out endpoint."""
    loop = asyncio.get_event_loop()
    text = await loop.run_in_executor(
        None, _generate, req.prompt, req.max_new_tokens, req.temperature, req.top_p
    )

    if req.stream:
        return StreamingResponse(
            (chunk for chunk in word_stream(text)),
            media_type="text/plain",
        )

    return {"response": text}


@app.post("/chat")
async def chat(req: ChatRequest):
    """Multi-turn chat endpoint using Gemma instruct format."""
    prompt = build_prompt(req.messages)
    loop = asyncio.get_event_loop()
    text = await loop.run_in_executor(
        None, _generate, prompt, req.max_new_tokens, req.temperature, req.top_p
    )

    if req.stream:
        return StreamingResponse(
            (chunk for chunk in word_stream(text)),
            media_type="text/plain",
        )

    return {"role": "assistant", "content": text}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=6000)
