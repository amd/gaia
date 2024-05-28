import time
from lemonade import leap

start = time.time()
model, tokenizer = leap.from_pretrained(
    "meta-llama/Llama-2-7b-chat-hf", recipe="ryzenai-npu"
)
print(f"Model creation took: {time.time()-start} secs")

messages = [
    "Hey, what's your name?",
    "What's the meaning of life?",
    "What's 42?"
]
for message in messages:
    start = time.time()
    input_ids = tokenizer(message, return_tensors="pt").input_ids
    response = model.generate(input_ids, max_new_tokens=30)
    
    print(tokenizer.decode(response[0]))
    print(f"{time.time()-start} secs")
