# Fix Code Testbench

A testbench for evaluating code-fixing prompts against LLM models (local or Claude). This tool helps validate and iterate on prompts used by agents to automatically fix code errors.

## Purpose

The testbench simulates code-fixing prompt workflows used by agents:
1. Takes a buggy source file and an error description
2. Sends a formatted prompt to an LLM (local endpoint or Claude Sonnet 4.5)
3. Extracts the model's suggested fix
4. Outputs the corrected code and displays a diff

It supports two modes:
- **`fix_code` mode** (default): Requests the full corrected code snippet
- **`edit_file` mode** (`--use-edit-file`): Requests an `edit_file` tool call with old/new content pairs


## Usage

### Basic Example

```bash
gaia eval fix-code path/to/buggy_file.py "Error message" output.py
```

### With Local Model

```
gaia eval fix-code \
    examples/average-calc.py \
    "NameError: name 'number' is not defined" \
    examples/average-calc-fixed.py \
    --model Qwen3-Coder-30B-A3B-Instruct-GGUF
```

All of the script flags (`--use-claude`, `--use-edit-file`, `--context`, `--start-line`, etc.) are available with identical semantics when invoked via `gaia eval fix-code`.

### With Claude

```bash
export ANTHROPIC_API_KEY=your_key_here
gaia eval fix-code \
    examples/max-value.py \
    "TypeError: 'NoneType' object is not callable" \
    examples/max-value-fixed.py \
    --use-claude
```

### Using edit_file Mode

```bash
gaia eval fix-code \
    examples/sum.py \
    "This is resulting in 10 but the correct answer is 15. Analyze and fix what is wrong." \
    examples/sum-fixed.py \
    --use-edit-file
```

## Example Output

Testing if a model can fix a simple off-by-one error:

```bash
gaia eval fix-code \
    examples/sum.py \
    "This is resulting in 10 but the correct answer is 15. Analyze and fix what is wrong." \
    examples/sum-fixed.py
```

The tool displays the prompt sent to the model, the raw response, and a diff showing the fix:

````text
=== Prompt ===
Fix the following Python code error:
File path: examples/sum.py
Error: This is resulting in 10 but the correct answer is 15. Analyze and fix what is wrong.
Code:

```python

"""Compute a numeric series."""
def sum(n):
    if n < 0:
        raise ValueError("n must be non-negative")
    total = 0
    for i in range(n):
        total += i
    return total

print(f"Sum through 5: {sum(5)}")
```

Return ONLY the corrected code, no explanations.

=== RAW RESPONSE ===

```python
"""Compute a numeric series."""
def sum(n):
    if n < 0:
        raise ValueError("n must be non-negative")
    total = 0
    for i in range(n + 1):
        total += i
    return total
print(f"Sum through 5: {sum(5)}")
```

=== Diff (cleaned vs original) ===

--- examples/sum.py:1-16\
+++ examples/sum-fixed.py:1-16
@@ -5,7 +5,7 @@
     if n < 0:
         raise ValueError("n must be non-negative")
     total = 0
-    for i in range(n):
+    for i in range(n + 1):
         total += i
     return total
````

Testing if a model can fix a variable name typo:

```bash
gaia eval fix-code \
    examples/average-calc.py \
    "File \"/scratch/eddier/gaia/src/gaia/eval/fix_code_testbench/examples/average-calc.py\", line 16, in <module>" \
    examples/average-calc-fixed.py
```

This tests the model's ability to identify and fix a simple typo where `number` is used instead of `numbers` on line 12.

Testing if a model can fix a mutable default argument bug:

```bash
gaia eval fix-code \
    examples/append.py \
    "Default list is shared across calls. Fix this." \
    examples/append-fixed.py
```

This tests the model's ability to recognize and fix the common Python anti-pattern where a mutable default argument (like a list) is shared across function calls.

### Real-World Use Case: TypeScript Type Error 

In an earlier version of the web dev agent, we were creating web apps that failed TypeScript validation. For example, the `examples/workout_web_app_component/WorkoutForm.tsx` component had a TypeScript error that could be discovered when running TypeScript validation:

```
src/components/WorkoutForm.tsx:43:9 - error TS2322: Type 'string' is not assignable to type 'never'.

40         normalized[field as keyof typeof normalized] = parsedValue.toISOString().
```

There were two common failure modes that the agent was running into when leverage a local LLM.

### Scenario 1: Giving the entire error message to the agent

Sometimes, the agent does not split the errors into multiple different tool calls. We can recreate this by putting two different error messages in the prompt, with the second being the one that corresponds to the error we are seeing.

```bash
gaia eval fix-code \
    examples/workout_web_app_component/WorkoutForm.tsx \
    "src/components/WorkoutForm.tsx:33:21 - error TS7053: Element implicitly has an 'any' type because expression of type 'string' can't be used to index type '{ name: string; duration: number; date: string; goal: string; }'. No index signature with a parameter of type 'string' was found on type '{ name: string; duration: number; date: string; goal: string; }'. 33  const value = normalized[field]; src/components/WorkoutForm.tsx:43:9 - error TS2322: Type 'string' is not assignable to type 'never'. 43 normalized[field as keyof typeof normalized] = parsedValue.toISOString()" \
    examples/workout_web_app_component/WorkoutForm-new.tsx
``` 

This will produce the following diff which does not fix the issue:

```
--- examples/workout_web_app_component/WorkoutForm.tsx:1-184
+++ examples/workout_web_app_component/WorkoutForm-new.tsx:1-184                                                                                                                                                                                                                                                                                                      
@@ -40,7 +40,7 @@                                                                                                                                                                                                                                                                                                             
       const parsedValue = new Date(value as string | number | Date);
       if (!Number.isNaN(parsedValue.getTime())) {
-        normalized[field as keyof typeof normalized] = parsedValue.toISOString();
+        normalized[field as keyof typeof normalized] = parsedValue.toISOString() as any;
       }
     });
```

### Scenario 2: Not providing the exact line of failure

Another failure mode was not providing enough specific information to the agent. In this case, sometimes the agent's ability to copy the entire error message is not working, and thus the additional line that shows the line corresponding to the failure is not provided to the model. This can be recreated in the testbench using the following. Note that the failing line number is still provided to the agent, but without the code associated with that line, the agent is unable to debug the issue.

```bash
gaia eval fix-code \
    examples/workout_web_app_component/WorkoutForm.tsx \
    "src/components/WorkoutForm.tsx:43:9 - error TS2322: Type 'string' is not assignable to type 'never'. " \
    examples/workout_web_app_component/WorkoutForm-new.tsx
```

This results in no diff created, presumably as the agent is not able to identify the failing line.

### Scenario: Only providing a single error with the appropriate failing line

We can recreate what would happen if we avoid the two previous failure modes which are never able to fix the bug with the following:

```bash
gaia eval fix-code \
    examples/workout_web_app_component/WorkoutForm.tsx \
    "src/components/WorkoutForm.tsx:43:9 - error TS2322: Type 'string' is not assignable to type 'never'. 43 normalized[field as keyof typeof normalized] = parsedValue.toISOString();" \
    examples/workout_web_app_component/WorkoutForm-new.tsx
```

This will actually fix the issue ~50% of the time, unfortunately the other ~50% of the time it will create the following diff which does not fix the issue:

```
=== Diff (cleaned vs original) ===
--- examples/workout_web_app_component/WorkoutForm.tsx:1-184
+++ examples/workout_web_app_component/WorkoutForm-new.tsx:1-184
@@ -40,7 +40,7 @@
 
       const parsedValue = new Date(value as string | number | Date);
       if (!Number.isNaN(parsedValue.getTime())) {
-        normalized[field as keyof typeof normalized] = parsedValue.toISOString();
+        normalized[field as keyof typeof normalized] = parsedValue.toISOString() as unknown as (string | number);
       }
     });
```

### Scenario: Running with a frontier model

One test we want to ensure is that if a frontier model is able to fix the issues with . We can test this out with the testbench using the `--use-claude` flag. The prompts of the three scenarios shown above are all able to resolve the issue using Claude Sonnet 4.5.

So the question is, is there context engineering that we can do to have the local language model also be able to fix the bug. This is where the testbench can be very useful, as it allows agent designers to hone in on specific prompts, context, and tool definitions and see what enables the model to actually solve the bug. For this specific use case, we found two different solutions enable the model to be able to solve the issue consistently


### Solution 1: More specific prompt engineering

There is a flag in the testbench called `--use-prompt-engineering` which will add targeted guidance specifically at the end of the prompt about this specific TypeScript assignability issue. With this flag, the local model is able to provide a correct fix:

```bash
gaia eval fix-code \
    examples/workout_web_app_component/WorkoutForm.tsx \
    "src/components/WorkoutForm.tsx:43:9 - error TS2322: Type 'string' is not assignable to type 'never'. 43 normalized[field as keyof typeof normalized] = parsedValue.toISOString();" \
    examples/workout_web_app_component/WorkoutForm-new.tsx \
    --use-prompt-engineering
```

### Solution 2: Homing in on where the issue is occurring

Alternatively, the same correct solution can be achieved by focusing the model on just the relevant code section using `--start-line` and `--end-line`. 

```bash
gaia eval fix-code \
    examples/workout_web_app_component/WorkoutForm.tsx \
    "src/components/WorkoutForm.tsx:43:9 - error TS2322: Type 'string' is not assignable to type 'never'. 43 normalized[field as keyof typeof normalized] = parsedValue.toISOString();" \
    examples/workout_web_app_component/WorkoutForm-new.tsx \
    --start-line 35 \
    --end-line 45
```

Both approaches produce the same correct diff:

```
=== Diff (cleaned vs original) ===
--- examples/workout_web_app_component/WorkoutForm.tsx:1-181
+++ examples/workout_web_app_component/WorkoutForm-new.tsx:1-181
@@ -37,7 +37,7 @@
 
       const parsedValue = new Date(value as string | number | Date);
       if (!Number.isNaN(parsedValue.getTime())) {
-        normalized[field as keyof typeof normalized] = parsedValue.toISOString();
+        normalized[field as keyof typeof normalized] = parsedValue.toISOString() as unknown as never;
       }
     });
```

**Note:** While the local model with prompt engineering or line range limiting can produce a fix that resolves the TypeScript error, it's still not as desirable as Claude Sonnet 4.5's solution. The local model's fix uses type assertions (`as unknown as never`) which suppresses the error rather than addressing the root cause. Claude's solution properly types the `normalized` object, eliminating the need for complex type assertions. The local model's approach matches what the Cursor Tab model suggested, demonstrating parity with coding agents that people use everyday.

This demonstrates how the testbench enables experimentation with different prompt engineering techniques and context windowing strategies to improve a model's ability to fix specific types of errors.

### Common Options

- `--model MODEL`: Local model identifier (default: `Qwen3-Coder-30B-A3B-Instruct-GGUF`)
- `--use-claude`: Use Claude Sonnet 4.5 instead of local endpoint
- `--language LANG`: Override language detection (python/typescript/etc.)
- `--context TEXT`: Add additional context to the prompt
- `--use-prompt-engineering`: Inject targeted guidance into the prompt
- `--use-edit-file`: Request an `edit_file` tool call format instead of full code replacement
- `--start-line N`: Include only lines N onwards (default: 1)
- `--end-line N`: Include only up to line N (default: end of file)
- `--temperature FLOAT`: Sampling temperature (default: 0.2)
- `--timeout SECONDS`: HTTP timeout (default: 600)

## Test Cases

The directory includes example bug scenarios:
- `examples/average-calc.py`: Variable name typo
- `examples/max-value.py`: Function missing return statement
- `examples/append.py`: Mutable default argument issue
- `examples/sum.py`: Off-by-one loop error

## Requirements

- Python 3.x
- `openai` package (for local models)
- `anthropic` package (for Claude, when using `--use-claude`)
- Local LLM endpoint running at `http://localhost:8000/api/v1` (or set `ANTHROPIC_API_KEY` for Claude)
