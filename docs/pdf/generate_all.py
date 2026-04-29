"""Batch-convert Mintlify docs pages to PDF using Playwright."""
import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright

DOCS_DIR = Path("C:/Users/antmi/gaia/docs")
OUTPUT_DIR = DOCS_DIR / "pdf"
BASE_URL = "http://localhost:3000"

URLS = """
cpp/custom-agent
deployment/ui
eval
glossary
guides/agent-ui
guides/auto-spawn-pipeline
guides/blender
guides/chat
guides/component-framework
guides/docker
guides/emr
guides/explicit-tool-calling
guides/hardware-advisor
guides/jira
guides/mcp/agent-ui
guides/orchestration
guides/pipeline-canvas
guides/pipeline
guides/routing
index
plans/agent-ui
playbooks/chat-agent/part-2-advanced-features
playbooks/chat-agent/part-3-deployment
playbooks/hardware-advisor/index
quickstart
reference/api-spec
reference/api
reference/cli
reference/contributing-docs
reference/dev
reference/eval/fix-code-testbench
reference/features
reference/troubleshooting
releases/v0.15.1
releases/v0.16.1
releases/v0.17.0
releases/v0.17.1
roadmap
sdk/agents/specialized
sdk/agents/talk
sdk/api-reference
sdk/applications
sdk/core/agent-system
sdk/index
sdk/infrastructure/api-server
sdk/infrastructure/mcp
sdk/infrastructure/pipeline
sdk/sdks/agent-ui
sdk/sdks/audio
sdk/sdks/chat
sdk/sdks/rag
sdk/testing
setup
spec/agent-base
spec/agent-sdk
spec/agent-ui-eval-kpi-slides
spec/agent-ui-server
spec/blender-agent
spec/chat-agent
spec/component-status
spec/docker-agent
spec/electron-integration
spec/file-search-mixin
spec/jira-agent
spec/orchestrator
spec/pipeline-engine
spec/rag-sdk
spec/rag-tools-mixin
spec/summarizer-app
spec/talk-sdk
""".strip().splitlines()


def url_to_filename(url_path: str) -> str:
    """Convert a URL path to a safe PDF filename."""
    return url_path.replace("/", "_") + ".pdf"


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        total = len(URLS)
        success = 0
        failed = []

        for i, url_path in enumerate(URLS):
            url = f"{BASE_URL}/{url_path}"
            filename = url_to_filename(url_path)
            output = OUTPUT_DIR / filename
            try:
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await asyncio.sleep(2)  # let JS animations settle
                await page.pdf(
                    path=str(output),
                    format="A4",
                    print_background=True,
                    margin={"top": "20mm", "bottom": "20mm", "left": "15mm", "right": "15mm"},
                )
                success += 1
                print(f"[{i+1}/{total}] OK: {url_path} -> {filename}")
            except Exception as e:
                failed.append(url_path)
                print(f"[{i+1}/{total}] FAIL: {url_path} -> {e}")

        await browser.close()
        print(f"\nDone: {success}/{total} PDFs generated")
        if failed:
            print(f"Failed pages: {failed}")


if __name__ == "__main__":
    asyncio.run(main())
