Suggested PR body (summary + acceptance criteria)

Title: feat: hardware requirement validation for agents (Phase 1)

Summary
- Adds runtime validation for agent-declared hardware requirements.
- Agents may declare a `REQUIRED_HARDWARE: HardwareRequirement(min_device=...)`.
- At agent startup `LemonadeManager.ensure_ready(required_min_device=...)` queries the running Lemonade server via `LemonadeClient.get_system_info()` and validates that the host provides the declared capability tier.

Scope and limitations (Phase 1)
- This PR performs *validation only* and does NOT persist or read hardware from `~/.gaia/`.
- The resolved runtime "recipe" is computed for debugging and logging, but this PR does not apply the recipe to Lemonade server startup or model selection.
- A follow-up change can wire the resolved recipe into the server startup path if desired; that work is intentionally scoped out here to keep Phase 1 low-risk.

Why
- Provides an early fail-fast for agents that require specific hardware (e.g., NPU-only agents), preventing silently degraded behavior when a required device is absent.

Acceptance criteria / tests
- Unit tests cover dict-shaped and list-shaped `devices` payloads returned by `LemonadeClient.get_system_info()`.
- Tests assert missing/empty `devices` fallback to CPU behavior and that `REQUIRED_HARDWARE` enforcement raises a `HardwareRequirementError` when unmet.
- An end-to-end test verifies that an `Agent` subclass with `REQUIRED_HARDWARE` triggers the validation path during initialization.

Notes for reviewers
- The implementation queries the running Lemonade server at runtime and intentionally does not persist a hardware config file.
- If you want this PR to include the recipe->startup wiring, request it and I will scope the follow-up to a single additional commit that threads the resolved recipe to Lemonade startup.
