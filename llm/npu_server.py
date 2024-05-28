from fastapi import FastAPI, WebSocket
from pydantic import BaseModel
from lemonade import leap
import time

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
    response = model.generate(
        input_ids,
        max_new_tokens=300,
        # do_sample=True,
        # top_k=50,
        # top_p=0.95,
        # temperature=0.7,
        # pad_token_id=tokenizer.eos_token_id,
    )
    generated_text = tokenizer.decode(response[0], skip_special_tokens=True)

    # Remove the input prompt from the generated text
    generated_text = generated_text.replace(message.text, "").strip()

    return {"response": generated_text}


@app.websocket("/stream")
async def stream_response(websocket: WebSocket):
    await websocket.accept()
    message = await websocket.receive_text()
    input_ids = tokenizer(message, return_tensors="pt").input_ids

    # Set up the generation parameters
    generation_kwargs = {
        "max_new_tokens": 300,
        "do_sample": True,
        "top_k": 50,
        "top_p": 0.95,
        "temperature": 0.7,
        "pad_token_id": tokenizer.eos_token_id,
    }

    # Generate the response using streaming
    for response in model.stream_generate(input_ids, **generation_kwargs):
        generated_text = tokenizer.decode(response, skip_special_tokens=True)

        # Remove the input prompt from the generated text
        generated_text = generated_text.replace(message, "").strip()

        # Send the generated text to the client
        await websocket.send_text(generated_text)
        # Add a small delay to simulate streaming
        time.sleep(0.1)

    await websocket.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
