import requests

def test_server(message):
    url = "http://localhost:8000/generate"
    payload = {"text": message}
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()  # Raise an exception for 4xx or 5xx status codes
        data = response.json()
        generated_text = data["response"]
        print(f"Input: {message}")
        print(f"Generated Response: {generated_text}")

    except requests.exceptions.RequestException as e:
        print(f"Error: {e}")
    
    
if __name__ == "__main__":
    import time
    queries = [
        "give me the link to Turnkey tests",
        "give me the link to Turnkey instructions",
        "what is TurnkeyML's mission?",
        "how do I get started?",
        "where is the installation guide?",
        "how about tutorials?",
        "what use-cases are there?",
        "execute the demo.",
        "what was it benchmarked on?",
        "generated a report, collect the statistics and summarize in a spreadsheet",
    ]
    # queries = [
    #     "hello, what's your name?",
    # ]
    queries = [
        """\
<s>[INST] <<SYS>>
You are a helpful, respectful and honest assistant. Always answer as helpfully as possible, while being safe.  Your answers should not include any harmful, unethical, racist, sexist, toxic, dangerous, or illegal content. Please ensure that your responses are socially unbiased and positive in nature.

If a question does not make any sense, or is not factually coherent, explain why instead of answering something not correct. If you don't know the answer to a question, please don't share false information.
<</SYS>>

There's a llama in my garden 😱 What should I do? [/INST]
"""
    ]
    for query in queries:
        start = time.time()
        test_server(query)
        latency = time.time() - start
        print(f"{latency} secs\n")
        print('-------------------------------------------------------------------')