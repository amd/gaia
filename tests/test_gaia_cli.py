import pytest
from gaia.cli import run_cli

def test_gaia_cli_prompt():
    print("\nStarting test_gaia_cli_prompt...")

    # Start the servers
    print("Starting servers...")
    start_result = run_cli('start')
    print(f"Start result: {start_result}")
    assert start_result == "Servers started successfully."

    # Send a prompt and capture the response
    print("\nSending prompt...")
    response = run_cli('prompt', "hi")
    print(f"Response received:\n{response}")

    # Add your assertions here
    assert response is not None, "Response should not be None"
    assert isinstance(response, str), "Response should be a string"
    assert len(response) > 0, "Response should not be empty"

    # Stop the servers
    print("\nStopping servers...")
    stop_result = run_cli('stop')
    print(f"Stop result: {stop_result}")
    assert stop_result == "Servers stopped successfully."

    print("Test completed successfully.")

# Add more test functions as needed

if __name__ == "__main__":
    test_gaia_cli_prompt()
