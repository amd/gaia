# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import argparse
import json
from datetime import datetime
from pathlib import Path

from gaia.eval.claude import ClaudeClient
from gaia.eval.config import DEFAULT_CLAUDE_MODEL
from gaia.logger import get_logger


class EmailGenerator:
    """Generates example business emails for testing email processing and summarization."""

    def __init__(self, claude_model=None, max_tokens=8192):
        self.log = get_logger(__name__)

        # Initialize Claude client for dynamic content generation
        if claude_model is None:
            claude_model = DEFAULT_CLAUDE_MODEL
        try:
            self.claude_client = ClaudeClient(model=claude_model, max_tokens=max_tokens)
            self.log.info(f"Initialized Claude client with model: {claude_model}")
        except Exception as e:
            self.log.error(f"Failed to initialize Claude client: {e}")
            raise ValueError(
                f"Could not initialize Claude client. Please ensure ANTHROPIC_API_KEY is set. Error: {e}"
            )

        # Email templates with different use cases
        self.email_templates = {
            "project_update": {
                "description": "Project status update and milestone communication",
                "sender_roles": [
                    "Project Manager",
                    "Team Lead",
                    "Product Manager",
                    "Engineering Manager",
                ],
                "recipient_types": ["stakeholders", "team members", "management"],
                "context": "A professional project update email sharing progress, milestones achieved, upcoming deliverables, and any blockers or risks.",
            },
            "meeting_request": {
                "description": "Meeting invitation and scheduling coordination",
                "sender_roles": [
                    "Project Manager",
                    "Executive Assistant",
                    "Team Lead",
                    "Business Analyst",
                ],
                "recipient_types": ["team members", "stakeholders", "clients"],
                "context": "A formal meeting request email with agenda, purpose, scheduling details, and expectations for attendees.",
            },
            "customer_support": {
                "description": "Customer service response and issue resolution",
                "sender_roles": [
                    "Customer Support Representative",
                    "Technical Support Specialist",
                    "Account Manager",
                    "Support Team Lead",
                ],
                "recipient_types": ["customers", "clients", "users"],
                "context": "A professional customer support email addressing user issues, providing solutions, and maintaining positive customer relationships.",
            },
            "sales_outreach": {
                "description": "Sales prospecting and business development communication",
                "sender_roles": [
                    "Sales Representative",
                    "Business Development Manager",
                    "Account Executive",
                    "Sales Manager",
                ],
                "recipient_types": ["prospects", "leads", "potential clients"],
                "context": "A persuasive sales email introducing products or services, highlighting value propositions, and encouraging engagement.",
            },
            "internal_announcement": {
                "description": "Company-wide announcements and policy communications",
                "sender_roles": [
                    "CEO",
                    "HR Manager",
                    "Operations Manager",
                    "Communications Team",
                ],
                "recipient_types": ["all employees", "department teams", "management"],
                "context": "An official internal announcement covering company news, policy changes, organizational updates, or important notifications.",
            },
            "technical_discussion": {
                "description": "Technical problem-solving and architecture discussions",
                "sender_roles": [
                    "Senior Developer",
                    "Technical Architect",
                    "DevOps Engineer",
                    "CTO",
                ],
                "recipient_types": [
                    "development team",
                    "technical leads",
                    "engineering",
                ],
                "context": "A detailed technical email discussing system architecture, code reviews, technical challenges, or solution proposals.",
            },
            "vendor_communication": {
                "description": "External vendor and supplier coordination",
                "sender_roles": [
                    "Procurement Manager",
                    "Operations Manager",
                    "Project Manager",
                    "Finance Director",
                ],
                "recipient_types": ["vendors", "suppliers", "contractors"],
                "context": "A professional vendor communication covering contracts, deliverables, payment terms, or service requirements.",
            },
            "performance_feedback": {
                "description": "Employee performance reviews and feedback communication",
                "sender_roles": [
                    "HR Manager",
                    "Direct Manager",
                    "Team Lead",
                    "Director",
                ],
                "recipient_types": ["employees", "team members", "direct reports"],
                "context": "A constructive performance feedback email covering achievements, areas for improvement, and development opportunities.",
            },
        }

    def _estimate_tokens(self, text):
        """Rough token estimation (approximately 4 characters per token)."""
        return len(text) // 4

    def _generate_email_with_claude(self, email_type, target_tokens):
        """Generate an email using Claude based on the email type and target token count."""
        if email_type not in self.email_templates:
            raise ValueError(f"Unknown email type: {email_type}")

        template = self.email_templates[email_type]

        # Create a detailed prompt for Claude
        prompt = f"""Generate a realistic business email for the following scenario:

Email Type: {template['description']}
Context: {template['context']}
Sender Role Options: {', '.join(template['sender_roles'])}
Recipient Types: {', '.join(template['recipient_types'])}
Target Length: Approximately {target_tokens} tokens (about {target_tokens * 4} characters)

Please create a detailed, realistic business email that includes:
1. Professional email header (From, To, Subject, Date)
2. Appropriate greeting and professional tone
3. Clear purpose and main content relevant to the email type
4. Specific details, requests, or information as appropriate
5. Professional closing and signature
6. Realistic names, companies, and scenarios

Format the email as a complete email message with:
- Subject line
- Professional sender and recipient information
- Proper email structure and formatting
- Content appropriate for the business context

Make the email feel authentic and professional, with realistic details and appropriate tone for the email type. The email should be approximately {target_tokens} tokens long.

Generate only the email content, no additional commentary."""

        try:
            # Generate the email using Claude with usage tracking
            self.log.info(
                f"Generating {email_type} email with Claude (target: {target_tokens} tokens)"
            )
            response = self.claude_client.get_completion_with_usage(prompt)

            generated_content = (
                response["content"][0].text
                if isinstance(response["content"], list)
                else response["content"]
            )
            actual_tokens = self._estimate_tokens(generated_content)

            self.log.info(
                f"Generated email: {actual_tokens} tokens (target: {target_tokens})"
            )

            return generated_content, response["usage"], response["cost"]

        except Exception as e:
            self.log.error(f"Error generating email with Claude: {e}")
            raise RuntimeError(f"Failed to generate email for {email_type}: {e}")

    def _extend_content_with_claude(
        self, base_content, target_tokens, email_type, current_usage, current_cost
    ):
        """Extend existing content to reach target token count using Claude."""
        current_tokens = self._estimate_tokens(base_content)

        if current_tokens >= target_tokens:
            return base_content, current_usage, current_cost

        needed_tokens = target_tokens - current_tokens
        template = self.email_templates[email_type]

        extension_prompt = f"""Continue the following business email to make it approximately {needed_tokens} more tokens longer.

Current email:
{base_content}

Please add more realistic content that:
1. Maintains the same professional tone and context
2. Continues naturally from where it left off
3. Adds approximately {needed_tokens} more tokens of content
4. Includes meaningful details relevant to a {template['description']}
5. Maintains professional email format and structure

Generate only the additional email content (without repeating the existing content)."""

        try:
            self.log.info(f"Extending email by ~{needed_tokens} tokens")
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
            self.log.info(f"Extended email to {actual_tokens} tokens")

            return extended_content, total_usage, total_cost

        except Exception as e:
            self.log.error(f"Error extending email with Claude: {e}")
            # Return original content if extension fails
            return base_content, current_usage, current_cost

    def generate_email(self, email_type, target_tokens=800):
        """Generate a single business email of specified type and approximate token count using Claude."""
        if email_type not in self.email_templates:
            raise ValueError(f"Unknown email type: {email_type}")

        template = self.email_templates[email_type]

        try:
            # Generate email with Claude
            content, usage, cost = self._generate_email_with_claude(
                email_type, target_tokens
            )
            actual_tokens = self._estimate_tokens(content)

            # If we're significantly under target, try to extend
            if actual_tokens < target_tokens * 0.8:  # If less than 80% of target
                self.log.info(
                    f"Email too short ({actual_tokens} tokens), extending to reach target"
                )
                content, usage, cost = self._extend_content_with_claude(
                    content, target_tokens, email_type, usage, cost
                )
                actual_tokens = self._estimate_tokens(content)

            # Add metadata
            metadata = {
                "email_type": email_type,
                "description": template["description"],
                "sender_roles": template["sender_roles"],
                "recipient_types": template["recipient_types"],
                "estimated_tokens": actual_tokens,
                "target_tokens": target_tokens,
                "generated_date": datetime.now().isoformat(),
                "claude_model": self.claude_client.model,
                "claude_usage": usage,
                "claude_cost": cost,
            }

            return content, metadata

        except Exception as e:
            self.log.error(f"Failed to generate email for {email_type}: {e}")
            raise

    def generate_email_set(self, output_dir, target_tokens=800, count_per_type=1):
        """Generate a set of business emails and save them to the output directory."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        generated_files = []
        all_metadata = []
        total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        total_cost = {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0}

        for email_type in self.email_templates.keys():
            for i in range(count_per_type):
                self.log.info(f"Generating {email_type} email {i+1}/{count_per_type}")

                # Generate email
                content, metadata = self.generate_email(email_type, target_tokens)

                # Create filename
                if count_per_type == 1:
                    filename = f"{email_type}_email.txt"
                else:
                    filename = f"{email_type}_email_{i+1}.txt"

                # Save email file
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
                "email_types": list(self.email_templates.keys()),
                "files_per_type": count_per_type,
                "claude_model": self.claude_client.model,
                "total_claude_usage": total_usage,
                "total_claude_cost": total_cost,
            },
            "emails": all_metadata,
        }

        summary_path = output_dir / "email_metadata.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)

        self.log.info(f"Generated {len(generated_files)} email files in {output_dir}")
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
    """Command line interface for email generation."""
    parser = argparse.ArgumentParser(
        description="Generate example business emails using Claude AI for testing email processing and summarization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate one email of each type with ~800 tokens
  python -m gaia.eval.email_generator -o ./output/emails

  # Generate larger emails (~1500 tokens each)
  python -m gaia.eval.email_generator -o ./output/emails --target-tokens 1500

  # Generate multiple emails per type
  python -m gaia.eval.email_generator -o ./output/emails --count-per-type 3

  # Generate specific email types only
  python -m gaia.eval.email_generator -o ./output/emails --email-types project_update sales_outreach

  # Generate small emails for quick testing
  python -m gaia.eval.email_generator -o ./test_emails --target-tokens 400

  # Use different Claude model
  python -m gaia.eval.email_generator -o ./output/emails --claude-model claude-3-opus-20240229
        """,
    )

    parser.add_argument(
        "-o",
        "--output-dir",
        type=str,
        required=True,
        help="Output directory for generated email files",
    )
    parser.add_argument(
        "--target-tokens",
        type=int,
        default=800,
        help="Target token count per email (approximate, default: 800)",
    )
    parser.add_argument(
        "--count-per-type",
        type=int,
        default=1,
        help="Number of emails to generate per email type (default: 1)",
    )
    parser.add_argument(
        "--email-types",
        nargs="+",
        choices=[
            "project_update",
            "meeting_request",
            "customer_support",
            "sales_outreach",
            "internal_announcement",
            "technical_discussion",
            "vendor_communication",
            "performance_feedback",
        ],
        help="Specific email types to generate (default: all types)",
    )
    parser.add_argument(
        "--claude-model",
        type=str,
        default=None,
        help=f"Claude model to use for email generation (default: {DEFAULT_CLAUDE_MODEL})",
    )

    args = parser.parse_args()

    try:
        generator = EmailGenerator(claude_model=args.claude_model)
    except Exception as e:
        print(f"❌ Error initializing email generator: {e}")
        print("Make sure ANTHROPIC_API_KEY is set in your environment.")
        return 1

    try:
        # Filter email types if specified
        original_templates = None
        if args.email_types:
            # Temporarily filter the templates
            original_templates = generator.email_templates.copy()
            generator.email_templates = {
                k: v
                for k, v in generator.email_templates.items()
                if k in args.email_types
            }

        result = generator.generate_email_set(
            output_dir=args.output_dir,
            target_tokens=args.target_tokens,
            count_per_type=args.count_per_type,
        )

        print("✅ Successfully generated business emails")
        print(f"  Output directory: {result['output_directory']}")
        print(f"  Generated files: {len(result['generated_files'])}")
        print(f"  Metadata file: {result['metadata_file']}")

        # Show summary stats
        summary = result["summary"]
        generation_info = summary["generation_info"]
        total_tokens = generation_info["total_claude_usage"]["total_tokens"]
        total_cost = generation_info["total_claude_cost"]["total_cost"]
        avg_tokens = total_tokens / len(summary["emails"]) if summary["emails"] else 0

        print(f"  Total tokens used: {total_tokens:,}")
        print(f"  Total cost: ${total_cost:.4f}")
        print(f"  Average tokens per file: {avg_tokens:.0f}")
        print(f"  Average cost per file: ${total_cost/len(summary['emails']):.4f}")
        print(f"  Email types: {', '.join(generation_info['email_types'])}")
        print(f"  Claude model: {generation_info['claude_model']}")

        # Restore original templates if they were filtered
        if args.email_types and original_templates is not None:
            generator.email_templates = original_templates

    except Exception as e:
        print(f"❌ Error generating emails: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
