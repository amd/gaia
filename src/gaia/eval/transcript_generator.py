import json
import argparse
import random
from datetime import datetime, timedelta
from pathlib import Path
from gaia.logger import get_logger
from gaia.eval.claude import ClaudeClient


class TranscriptGenerator:
    """Generates example meeting transcripts for testing transcript summarization."""

    def __init__(self, claude_model="claude-sonnet-4-20250514", max_tokens=8192):
        self.log = get_logger(__name__)

        # Initialize Claude client for dynamic content generation
        try:
            self.claude_client = ClaudeClient(model=claude_model, max_tokens=max_tokens)
            self.log.info(f"Initialized Claude client with model: {claude_model}")
        except Exception as e:
            self.log.error(f"Failed to initialize Claude client: {e}")
            raise ValueError(
                f"Could not initialize Claude client. Please ensure ANTHROPIC_API_KEY is set. Error: {e}"
            )

        # Meeting templates with different use cases
        self.meeting_templates = {
            "standup": {
                "description": "Daily team standup meeting with status updates and blockers",
                "participants": [
                    "Alice Chen (Scrum Master)",
                    "Bob Martinez (Developer)",
                    "Carol Kim (Developer)",
                    "David Wilson (QA Engineer)",
                ],
                "duration_minutes": 15,
                "context": "A software development team's daily standup meeting where team members share their progress, current tasks, and any blockers they're facing.",
            },
            "planning": {
                "description": "Sprint planning meeting for upcoming development cycle",
                "participants": [
                    "Sarah Johnson (Product Owner)",
                    "Mike Thompson (Scrum Master)",
                    "Lisa Wang (Senior Developer)",
                    "Tom Rodriguez (Developer)",
                    "Emma Davis (UX Designer)",
                ],
                "duration_minutes": 60,
                "context": "A sprint planning session where the team reviews the product backlog, estimates story points, and commits to work for the upcoming sprint.",
            },
            "client_call": {
                "description": "Client requirements gathering and project discussion",
                "participants": [
                    "Jennifer Adams (Account Manager)",
                    "Robert Smith (Client - CTO)",
                    "Maria Garcia (Client - Product Manager)",
                    "Alex Brown (Technical Lead)",
                ],
                "duration_minutes": 45,
                "context": "A client meeting to discuss project requirements, gather feedback, and align on technical solutions and timeline.",
            },
            "design_review": {
                "description": "Technical design review for new system architecture",
                "participants": [
                    "Dr. Kevin Liu (Principal Architect)",
                    "Priya Patel (Senior Developer)",
                    "James Miller (DevOps Engineer)",
                    "Sophie Turner (Security Engineer)",
                    "Ryan O'Connor (Database Specialist)",
                ],
                "duration_minutes": 90,
                "context": "A technical architecture review meeting where the team discusses system design, evaluates trade-offs, and makes architectural decisions.",
            },
            "performance_review": {
                "description": "Quarterly performance review and goal setting",
                "participants": [
                    "Linda Zhang (Engineering Manager)",
                    "Chris Anderson (Senior Software Engineer)",
                ],
                "duration_minutes": 30,
                "context": "A one-on-one performance review meeting between a manager and employee to discuss accomplishments, areas for growth, and career goals.",
            },
            "all_hands": {
                "description": "Company all-hands meeting with quarterly updates",
                "participants": [
                    "Mark Taylor (CEO)",
                    "Rachel Green (CTO)",
                    "John Lee (VP Sales)",
                    "Amy White (VP Marketing)",
                ],
                "duration_minutes": 45,
                "context": "A company-wide meeting where leadership shares business updates, financial results, and strategic direction with all employees.",
            },
            "budget_planning": {
                "description": "Annual budget planning and resource allocation",
                "participants": [
                    "Patricia Brown (CFO)",
                    "Daniel Kim (VP Engineering)",
                    "Michelle Jones (VP Sales)",
                    "Steve Wilson (VP Marketing)",
                ],
                "duration_minutes": 75,
                "context": "A budget planning meeting where department heads discuss resource needs, budget allocations, and strategic investments for the upcoming year.",
            },
            "product_roadmap": {
                "description": "Product roadmap discussion and feature prioritization",
                "participants": [
                    "Nicole Davis (Product Manager)",
                    "Frank Chen (Engineering Lead)",
                    "Jessica Miller (Senior Designer)",
                    "Carlos Ruiz (Data Analyst)",
                ],
                "duration_minutes": 60,
                "context": "A product planning meeting to review customer feedback, prioritize features, and define the product roadmap for the next quarter.",
            },
        }

    def _estimate_tokens(self, text):
        """Rough token estimation (approximately 4 characters per token)."""
        return len(text) // 4

    def _generate_transcript_with_claude(self, meeting_type, target_tokens):
        """Generate a meeting transcript using Claude based on the meeting type and target token count."""
        if meeting_type not in self.meeting_templates:
            raise ValueError(f"Unknown meeting type: {meeting_type}")

        template = self.meeting_templates[meeting_type]

        # Create a detailed prompt for Claude
        prompt = f"""Generate a realistic meeting transcript for the following scenario:

Meeting Type: {template['description']}
Context: {template['context']}
Participants: {', '.join(template['participants'])}
Duration: {template['duration_minutes']} minutes
Target Length: Approximately {target_tokens} tokens (about {target_tokens * 4} characters)

Please create a detailed, realistic meeting transcript that includes:
1. Meeting header with date, time, and participants
2. Natural dialogue between the participants that reflects their roles
3. Realistic conversation flow appropriate for this type of meeting
4. Specific technical details, decisions, and action items where relevant
5. Natural interruptions, clarifications, and back-and-forth discussion

Format the transcript with speaker names followed by colons, like:
"Speaker Name: What they said"

Make the conversation feel authentic and professional, with each participant contributing meaningfully based on their role. The transcript should be approximately {target_tokens} tokens long.

Generate only the transcript content, no additional commentary."""

        try:
            # Generate the transcript using Claude with usage tracking
            self.log.info(
                f"Generating {meeting_type} transcript with Claude (target: {target_tokens} tokens)"
            )
            response = self.claude_client.get_completion_with_usage(prompt)

            generated_content = (
                response["content"][0].text
                if isinstance(response["content"], list)
                else response["content"]
            )
            actual_tokens = self._estimate_tokens(generated_content)

            self.log.info(
                f"Generated transcript: {actual_tokens} tokens (target: {target_tokens})"
            )

            return generated_content, response["usage"], response["cost"]

        except Exception as e:
            self.log.error(f"Error generating transcript with Claude: {e}")
            raise RuntimeError(f"Failed to generate transcript for {meeting_type}: {e}")

    def _extend_content_with_claude(
        self, base_content, target_tokens, meeting_type, current_usage, current_cost
    ):
        """Extend existing content to reach target token count using Claude."""
        current_tokens = self._estimate_tokens(base_content)

        if current_tokens >= target_tokens:
            return base_content, current_usage, current_cost

        needed_tokens = target_tokens - current_tokens
        template = self.meeting_templates[meeting_type]

        extension_prompt = f"""Continue the following meeting transcript to make it approximately {needed_tokens} more tokens longer.

Current transcript:
{base_content}

Please add more realistic dialogue that:
1. Maintains the same tone and context as the existing transcript
2. Continues naturally from where it left off
3. Adds approximately {needed_tokens} more tokens of content
4. Includes meaningful discussion relevant to a {template['description']}
5. Maintains the same participants and their roles

Generate only the additional transcript content (without repeating the existing content)."""

        try:
            self.log.info(f"Extending transcript by ~{needed_tokens} tokens")
            response = self.claude_client.get_completion_with_usage(extension_prompt)

            extension_content = (
                response["content"][0].text
                if isinstance(response["content"], list)
                else response["content"]
            )
            extended_content = base_content + "\n\n" + extension_content

            # Combine usage and cost data
            total_usage = {
                "input_tokens": current_usage["input_tokens"]
                + response["usage"]["input_tokens"],
                "output_tokens": current_usage["output_tokens"]
                + response["usage"]["output_tokens"],
                "total_tokens": current_usage["total_tokens"]
                + response["usage"]["total_tokens"],
            }

            total_cost = {
                "input_cost": current_cost["input_cost"]
                + response["cost"]["input_cost"],
                "output_cost": current_cost["output_cost"]
                + response["cost"]["output_cost"],
                "total_cost": current_cost["total_cost"]
                + response["cost"]["total_cost"],
            }

            actual_tokens = self._estimate_tokens(extended_content)
            self.log.info(f"Extended transcript to {actual_tokens} tokens")

            return extended_content, total_usage, total_cost

        except Exception as e:
            self.log.error(f"Error extending transcript with Claude: {e}")
            # Return original content if extension fails
            return base_content, current_usage, current_cost

    def generate_transcript(self, meeting_type, target_tokens=1000):
        """Generate a single meeting transcript of specified type and approximate token count using Claude."""
        if meeting_type not in self.meeting_templates:
            raise ValueError(f"Unknown meeting type: {meeting_type}")

        template = self.meeting_templates[meeting_type]

        try:
            # Generate transcript with Claude
            content, usage, cost = self._generate_transcript_with_claude(
                meeting_type, target_tokens
            )
            actual_tokens = self._estimate_tokens(content)

            # If we're significantly under target, try to extend
            if actual_tokens < target_tokens * 0.8:  # If less than 80% of target
                self.log.info(
                    f"Transcript too short ({actual_tokens} tokens), extending to reach target"
                )
                content, usage, cost = self._extend_content_with_claude(
                    content, target_tokens, meeting_type, usage, cost
                )
                actual_tokens = self._estimate_tokens(content)

            # Add metadata
            metadata = {
                "meeting_type": meeting_type,
                "description": template["description"],
                "participants": template["participants"],
                "estimated_duration_minutes": template["duration_minutes"],
                "estimated_tokens": actual_tokens,
                "target_tokens": target_tokens,
                "generated_date": datetime.now().isoformat(),
                "claude_model": self.claude_client.model,
                "claude_usage": usage,
                "claude_cost": cost,
            }

            return content, metadata

        except Exception as e:
            self.log.error(f"Failed to generate transcript for {meeting_type}: {e}")
            raise

    def generate_transcript_set(self, output_dir, target_tokens=1000, count_per_type=1):
        """Generate a set of meeting transcripts and save them to the output directory."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        generated_files = []
        all_metadata = []
        total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        total_cost = {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0}

        for meeting_type in self.meeting_templates.keys():
            for i in range(count_per_type):
                self.log.info(
                    f"Generating {meeting_type} transcript {i+1}/{count_per_type}"
                )

                # Generate transcript
                content, metadata = self.generate_transcript(
                    meeting_type, target_tokens
                )

                # Create filename
                if count_per_type == 1:
                    filename = f"{meeting_type}_meeting.txt"
                else:
                    filename = f"{meeting_type}_meeting_{i+1}.txt"

                # Save transcript file
                file_path = output_dir / filename
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)

                # Update metadata with file info
                metadata["filename"] = filename
                metadata["file_path"] = str(file_path)
                metadata["file_size_bytes"] = len(content.encode("utf-8"))

                generated_files.append(str(file_path))
                all_metadata.append(metadata)

                # Accumulate usage and cost
                usage = metadata["claude_usage"]
                cost = metadata["claude_cost"]
                total_usage["input_tokens"] += usage["input_tokens"]
                total_usage["output_tokens"] += usage["output_tokens"]
                total_usage["total_tokens"] += usage["total_tokens"]
                total_cost["input_cost"] += cost["input_cost"]
                total_cost["output_cost"] += cost["output_cost"]
                total_cost["total_cost"] += cost["total_cost"]

                self.log.info(
                    f"Generated {filename} ({metadata['estimated_tokens']} tokens, ${cost['total_cost']:.4f})"
                )

        # Create summary metadata file
        summary = {
            "generation_info": {
                "generated_date": datetime.now().isoformat(),
                "total_files": len(generated_files),
                "target_tokens_per_file": target_tokens,
                "meeting_types": list(self.meeting_templates.keys()),
                "files_per_type": count_per_type,
                "claude_model": self.claude_client.model,
                "total_claude_usage": total_usage,
                "total_claude_cost": total_cost,
            },
            "transcripts": all_metadata,
        }

        summary_path = output_dir / "transcript_metadata.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)

        self.log.info(
            f"Generated {len(generated_files)} transcript files in {output_dir}"
        )
        self.log.info(
            f"Total cost: ${total_cost['total_cost']:.4f} ({total_usage['total_tokens']:,} tokens)"
        )
        self.log.info(f"Summary metadata saved to {summary_path}")

        return {
            "output_directory": str(output_dir),
            "generated_files": generated_files,
            "metadata_file": str(summary_path),
            "summary": summary,
        }


def main():
    """Command line interface for transcript generation."""
    parser = argparse.ArgumentParser(
        description="Generate example meeting transcripts using Claude AI for testing transcript summarization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate one transcript of each type with ~1000 tokens
  python -m gaia.eval.transcript_generator -o ./output/transcripts

  # Generate larger transcripts (~3000 tokens each)
  python -m gaia.eval.transcript_generator -o ./output/transcripts --target-tokens 3000

  # Generate multiple transcripts per type
  python -m gaia.eval.transcript_generator -o ./output/transcripts --count-per-type 3

  # Generate specific meeting types only
  python -m gaia.eval.transcript_generator -o ./output/transcripts --meeting-types standup planning

  # Generate small transcripts for quick testing
  python -m gaia.eval.transcript_generator -o ./test_transcripts --target-tokens 500

  # Use different Claude model
  python -m gaia.eval.transcript_generator -o ./output/transcripts --claude-model claude-3-opus-20240229
        """,
    )

    parser.add_argument(
        "-o",
        "--output-dir",
        type=str,
        required=True,
        help="Output directory for generated transcript files",
    )
    parser.add_argument(
        "--target-tokens",
        type=int,
        default=1000,
        help="Target token count per transcript (approximate, default: 1000)",
    )
    parser.add_argument(
        "--count-per-type",
        type=int,
        default=1,
        help="Number of transcripts to generate per meeting type (default: 1)",
    )
    parser.add_argument(
        "--meeting-types",
        nargs="+",
        choices=[
            "standup",
            "planning",
            "client_call",
            "design_review",
            "performance_review",
            "all_hands",
            "budget_planning",
            "product_roadmap",
        ],
        help="Specific meeting types to generate (default: all types)",
    )
    parser.add_argument(
        "--claude-model",
        type=str,
        default="claude-sonnet-4-20250514",
        help="Claude model to use for transcript generation (default: claude-sonnet-4-20250514)",
    )

    args = parser.parse_args()

    try:
        generator = TranscriptGenerator(claude_model=args.claude_model)
    except Exception as e:
        print(f"❌ Error initializing transcript generator: {e}")
        print("Make sure ANTHROPIC_API_KEY is set in your environment.")
        return 1

    try:
        # Filter meeting types if specified
        if args.meeting_types:
            # Temporarily filter the templates
            original_templates = generator.meeting_templates.copy()
            generator.meeting_templates = {
                k: v
                for k, v in generator.meeting_templates.items()
                if k in args.meeting_types
            }

        result = generator.generate_transcript_set(
            output_dir=args.output_dir,
            target_tokens=args.target_tokens,
            count_per_type=args.count_per_type,
        )

        print("✅ Successfully generated meeting transcripts")
        print(f"  Output directory: {result['output_directory']}")
        print(f"  Generated files: {len(result['generated_files'])}")
        print(f"  Metadata file: {result['metadata_file']}")

        # Show summary stats
        summary = result["summary"]
        generation_info = summary["generation_info"]
        total_tokens = generation_info["total_claude_usage"]["total_tokens"]
        total_cost = generation_info["total_claude_cost"]["total_cost"]
        avg_tokens = (
            total_tokens / len(summary["transcripts"]) if summary["transcripts"] else 0
        )

        print(f"  Total tokens used: {total_tokens:,}")
        print(f"  Total cost: ${total_cost:.4f}")
        print(f"  Average tokens per file: {avg_tokens:.0f}")
        print(f"  Average cost per file: ${total_cost/len(summary['transcripts']):.4f}")
        print(f"  Meeting types: {', '.join(generation_info['meeting_types'])}")
        print(f"  Claude model: {generation_info['claude_model']}")

        # Restore original templates if they were filtered
        if args.meeting_types:
            generator.meeting_templates = original_templates

    except Exception as e:
        print(f"❌ Error generating transcripts: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
