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
    test_server("hello, what's your name?")