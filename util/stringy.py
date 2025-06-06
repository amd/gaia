import re
import json
from typing import Optional

def format_prompt(prompt: str, indent: int = 0) -> str:
    """
    Format a prompt string to be more readable in the terminal.
    Handles newlines, JSON blocks, and indentation.

    Args:
        prompt: The raw prompt string
        indent: Number of spaces to indent each line (default: 0)

    Returns:
        Formatted prompt string
    """
    # Split into sections
    sections = prompt.split("\n\n")
    formatted_sections = []
    
    for section in sections:
        # Handle JSON blocks
        if "```json" in section:
            # Extract and format JSON blocks
            json_blocks = re.findall(r'```json\n(.*?)\n```', section, re.DOTALL)
            for block in json_blocks:
                try:
                    # Parse and format JSON with proper indentation
                    json_obj = json.loads(block)
                    formatted_json = json.dumps(json_obj, indent=2)
                    # Replace original block with formatted JSON
                    section = section.replace(block, formatted_json)
                except json.JSONDecodeError:
                    pass

        # Handle tool descriptions
        if "Args:" in section or "Returns:" in section:
            # Add extra newline before Args/Returns
            section = re.sub(r'(\n\s*)(Args:|Returns:)', r'\n\1\2', section)
            
            # Format any JSON-like structures
            json_like = re.findall(r'({.*?})', section, re.DOTALL)
            for j in json_like:
                try:
                    json_obj = json.loads(j)
                    formatted_json = json.dumps(json_obj, indent=2)
                    section = section.replace(j, formatted_json)
                except json.JSONDecodeError:
                    pass

        formatted_sections.append(section)

    # Join sections with double newlines
    formatted = "\n\n".join(formatted_sections)
    
    # Replace escaped newlines with actual newlines
    formatted = formatted.replace("\\n", "\n")
    
    # Remove escape characters from quotes
    formatted = formatted.replace('\\"', '"')

    # Remove other common escape characters
    formatted = formatted.replace('\\t', '\t')
    formatted = formatted.replace('\\r', '\r')
    formatted = formatted.replace('\\\\', '\\')

    # Apply indentation
    if indent > 0:
        lines = formatted.split("\n")
        formatted = "\n".join(" " * indent + line for line in lines)
    
    return formatted

def print_prompt(prompt: str, indent: int = 0, title: Optional[str] = None) -> None:
    """
    Print a formatted prompt to the terminal.

    Args:
        prompt: The raw prompt string
        indent: Number of spaces to indent each line (default: 0)
        title: Optional title to print before the prompt
    """
    formatted = format_prompt(prompt, indent)
    
    if title:
        print(f"\n{title}")
        print("=" * len(title))
    
    print(formatted)
    print()  # Add extra newline at end

def get_multiline_input() -> str:
    """
    Get multiline input from the user until they enter an empty line.

    Returns:
        The complete input string
    """
    print("Enter your prompt (press Enter twice to finish):")
    lines = []
    while True:
        line = input()
        if line == "":
            break
        lines.append(line)
    return "\n".join(lines)

if __name__ == "__main__":
    print("Welcome to the Prompt Formatter!")
    print("This tool helps format prompts with proper indentation and JSON formatting.")
    print("Type 'exit' to quit or press Enter twice to format your input.")
    print()

    while True:
        try:
            # Get input from user
            prompt = get_multiline_input()

            # Check for exit command
            if prompt.lower() == "exit":
                print("Goodbye!")
                break

            # Skip empty input
            if not prompt.strip():
                continue

            # Format and print the prompt
            print_prompt(prompt, title="Formatted Prompt")

            # Ask if user wants to continue
            response = input("Format another prompt? (y/n): ").lower()
            if response != 'y':
                print("Goodbye!")
                break

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {str(e)}")
            print("Please try again.")
