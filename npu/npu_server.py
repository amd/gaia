from fastapi import FastAPI
from pydantic import BaseModel
from lemonade import leap

app = FastAPI()

# Load the model and tokenizer
model, tokenizer = leap.from_pretrained(
    "meta-llama/Llama-2-7b-chat-hf", recipe="ryzenai-npu"
)

class Message(BaseModel):
    text: str

@app.post("/generate")
async def generate_response(message: Message):
    input_ids = tokenizer(message.text, return_tensors="pt").input_ids
    response = model.generate(input_ids, temperature = 0.7, max_new_tokens=30)
    generated_text = tokenizer.decode(response[0], skip_special_tokens=True)

    # Remove the input prompt from the generated text
    generated_text = generated_text.replace(message.text, "").strip()

    return {"response": generated_text}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
