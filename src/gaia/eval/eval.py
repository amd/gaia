# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from gaia.eval.claude import ClaudeClient
from gaia.logger import get_logger


class Evaluator:
    """Evaluates AI model performance across various use cases (summarization, Q&A, RAG, etc.)."""

    def __init__(self, model="claude-sonnet-4-20250514"):
        self.log = get_logger(__name__)
        # Increase max_tokens to 4096 to avoid truncation of complex JSON responses
        self.claude = ClaudeClient(model=model, max_tokens=4096)

    def calculate_similarity(self, text1: str, text2: str) -> float:
        """
        Calculate cosine similarity between two texts using TF-IDF vectors.

        Args:
            text1: First text (ground truth)
            text2: Second text (response)

        Returns:
            float: Cosine similarity score between 0 and 1
        """
        if not text1.strip() or not text2.strip():
            return 0.0

        try:
            vectorizer = TfidfVectorizer(stop_words="english", lowercase=True)
            vectors = vectorizer.fit_transform([text1, text2])
            similarity = cosine_similarity(vectors[0:1], vectors[1:2])[0][0]
            return float(similarity)
        except Exception as e:
            self.log.warning(f"Error calculating similarity: {e}")
            return 0.0

    def determine_pass_fail(
        self, similarity: float, threshold: float, claude_analysis: Dict = None
    ) -> Dict:
        """
        Determine pass/fail based on comprehensive evaluation criteria.

        Args:
            similarity: Similarity score between ground truth and response
            threshold: Similarity threshold
            claude_analysis: Claude's qualitative analysis (correctness, completeness, etc.)

        Returns:
            Dict containing pass/fail determination and reasoning
        """
        # Start with similarity-based evaluation
        similarity_pass = similarity >= threshold

        # If no Claude analysis available, fall back to similarity only
        if not claude_analysis:
            return {
                "is_pass": similarity_pass,
                "pass_fail": "pass" if similarity_pass else "fail",
                "criteria": "similarity_only",
                "reasoning": f"Similarity score {similarity:.3f} {'meets' if similarity_pass else 'below'} threshold {threshold:.3f}",
            }

        # Extract Claude's ratings
        ratings = {}
        for criterion in ["correctness", "completeness", "conciseness", "relevance"]:
            if criterion in claude_analysis:
                rating = claude_analysis[criterion].get("rating", "").lower()
                ratings[criterion] = rating

        # Define scoring system: excellent=4, good=3, fair=2, poor=1
        score_map = {"excellent": 4, "good": 3, "fair": 2, "poor": 1}

        # Calculate weighted scores (correctness and completeness are more important)
        weights = {
            "correctness": 0.4,
            "completeness": 0.3,
            "conciseness": 0.15,
            "relevance": 0.15,
        }

        total_score = 0
        max_possible = 0
        criteria_details = []

        for criterion, weight in weights.items():
            if criterion in ratings:
                rating = ratings[criterion]
                score = score_map.get(rating, 1)
                weighted_score = score * weight
                total_score += weighted_score
                max_possible += 4 * weight
                criteria_details.append(f"{criterion}: {rating} ({score}/4)")

        # Calculate normalized score (0-1)
        normalized_score = total_score / max_possible if max_possible > 0 else 0

        # Determine pass/fail using combined criteria:
        # 1. Must meet minimum qualitative threshold (normalized score >= 0.6)
        # 2. Correctness must be at least "fair"
        # 3. Either high similarity OR good qualitative scores can pass

        correctness_acceptable = ratings.get("correctness", "poor") in [
            "fair",
            "good",
            "excellent",
        ]
        qualitative_pass = normalized_score >= 0.6 and correctness_acceptable

        # Final determination: pass if either high similarity OR good qualitative scores
        final_pass = similarity_pass or qualitative_pass

        # Override: fail if correctness is "poor" regardless of other factors
        if ratings.get("correctness", "") == "poor":
            final_pass = False

        reasoning_parts = [
            f"Similarity: {similarity:.3f} ({'✓' if similarity_pass else '✗'} threshold {threshold:.3f})",
            f"Qualitative score: {normalized_score:.2f} ({'✓' if qualitative_pass else '✗'} ≥0.6)",
            f"Correctness: {ratings.get('correctness', 'N/A')} ({'✓' if correctness_acceptable else '✗'} ≥fair)",
        ]

        return {
            "is_pass": final_pass,
            "pass_fail": "pass" if final_pass else "fail",
            "criteria": "comprehensive",
            "reasoning": "; ".join(reasoning_parts),
            "scores": {
                "similarity": similarity,
                "qualitative_normalized": normalized_score,
                "qualitative_details": criteria_details,
            },
        }

    def load_results(self, results_path: str) -> Dict:
        """Load test results from a JSON file."""
        try:
            with open(results_path, "r") as f:
                return json.load(f)
        except Exception as e:
            self.log.error(f"Error loading results file: {e}")
            raise

    def check_evaluation_exists(self, experiment_file: str, output_dir: str) -> bool:
        """Check if evaluation already exists for experiment file.

        Args:
            experiment_file: Path to the experiment file
            output_dir: Output directory for evaluations

        Returns:
            True if evaluation file already exists, False otherwise
        """
        experiment_path = Path(experiment_file)
        output_base_path = Path(output_dir)

        # Generate expected eval filename: <name>.experiment.eval.json
        eval_filename = f"{experiment_path.stem}.eval.json"

        # Check for hierarchical structure first
        relative_path = None
        if "experiments" in experiment_path.parts:
            # Extract relative path from experiments directory
            exp_idx = experiment_path.parts.index("experiments")
            if exp_idx + 1 < len(experiment_path.parts):
                relative_path = Path(*experiment_path.parts[exp_idx + 1 : -1])

        # Check both locations: hierarchical and flat
        eval_paths = []
        if relative_path:
            eval_paths.append(output_base_path / relative_path / eval_filename)
        eval_paths.append(output_base_path / eval_filename)

        for eval_path in eval_paths:
            if eval_path.exists():
                self.log.info(f"Evaluation already exists: {eval_path}")
                return True

        return False

    def evaluate(self, results_path: str) -> Dict:
        """
        Evaluate RAG results and generate metrics.

        Args:
            results_path: Path to the results JSON file

        Returns:
            Dict containing evaluation metrics
        """
        results = self.load_results(results_path)
        qa_results = results["analysis"]["qa_results"]

        # Calculate similarity scores and pass/fail during evaluation
        similarities = []
        pass_results = []
        threshold = results["metadata"]["similarity_threshold"]

        for result in qa_results:
            similarity = self.calculate_similarity(
                result["ground_truth"], result["response"]
            )
            similarities.append(similarity)
            pass_results.append(similarity >= threshold)

        # Calculate accuracy metrics
        total_questions = len(pass_results)
        passed_questions = sum(pass_results)
        failed_questions = total_questions - passed_questions
        accuracy = passed_questions / total_questions if total_questions > 0 else 0.0

        metrics = {
            "test_file": results["metadata"]["test_file"],
            "timestamp": results["metadata"]["timestamp"],
            "threshold": results["metadata"]["similarity_threshold"],
            "num_questions": len(qa_results),
            "similarity_scores": {
                "mean": float(np.mean(similarities)),
                "median": float(np.median(similarities)),
                "std": float(np.std(similarities)),
                "min": float(np.min(similarities)),
                "max": float(np.max(similarities)),
            },
            "threshold_metrics": {
                "num_passed": passed_questions,
                "num_failed": failed_questions,
                "accuracy": accuracy,
                "accuracy_percentage": accuracy * 100.0,
            },
        }

        # Calculate pass rate
        metrics["threshold_metrics"]["pass_rate"] = (
            metrics["threshold_metrics"]["num_passed"] / metrics["num_questions"]
        )

        # Add overall rating based on pass rate and mean similarity
        pass_rate = metrics["threshold_metrics"]["pass_rate"]
        mean_similarity = metrics["similarity_scores"]["mean"]

        if pass_rate >= 0.9 and mean_similarity >= 0.8:
            rating = "excellent"
        elif pass_rate >= 0.8 and mean_similarity >= 0.7:
            rating = "good"
        elif pass_rate >= 0.6 and mean_similarity >= 0.6:
            rating = "fair"
        else:
            rating = "poor"

        metrics["overall_rating"] = {
            "rating": rating,
            "pass_rate": pass_rate,
            "mean_similarity": mean_similarity,
        }

        return metrics

    def analyze_with_claude(
        self, results_path: str, groundtruth_path: Optional[str] = None
    ) -> Dict:
        """
        Use Claude to perform qualitative analysis of RAG results.

        Args:
            results_path: Path to results JSON file
            groundtruth_path: Optional path to groundtruth file for comparison

        Returns:
            Dict containing Claude's analysis
        """
        # Start timing
        start_time = time.time()

        try:
            results = self.load_results(results_path)

            # Detect result type and extract appropriate data
            analysis_data = results.get("analysis", {})
            qa_results = analysis_data.get("qa_results", results.get("qa_results", []))
            summarization_results = analysis_data.get("summarization_results", [])

            # Determine evaluation type
            if qa_results:
                return self._analyze_qa_results(results, qa_results)
            elif summarization_results:
                return self._analyze_summarization_results(
                    results, summarization_results, groundtruth_path
                )
            else:
                return {
                    "overall_analysis": "No QA or summarization results found to analyze",
                    "strengths": [],
                    "weaknesses": ["No data available for analysis"],
                    "recommendations": [
                        "Ensure input data contains QA or summarization results"
                    ],
                    "use_case_fit": "Unable to determine",
                    "per_question": [],
                    "overall_rating": {
                        "rating": "error",
                        "explanation": "No analyzable results found",
                    },
                    "timing": {
                        "total_processing_time_seconds": round(
                            time.time() - start_time, 3
                        )
                    },
                }
        except Exception as e:
            self.log.error(f"Error in analyze_with_claude: {e}")
            return {
                "overall_analysis": f"Analysis failed: {str(e)}",
                "strengths": [],
                "weaknesses": ["Analysis failed to complete"],
                "recommendations": ["Check logs for error details"],
                "use_case_fit": "",
                "per_question": [],
                "overall_rating": {"rating": "error", "explanation": str(e)},
                "timing": {
                    "total_processing_time_seconds": round(time.time() - start_time, 3)
                },
            }

    def _analyze_qa_results(self, results: Dict, qa_results: List) -> Dict:
        """Analyze QA results using Claude."""
        # Start timing
        analysis_start_time = time.time()

        # Initialize analysis structure
        analysis = {
            "overall_analysis": "",
            "strengths": [],
            "weaknesses": [],
            "recommendations": [],
            "use_case_fit": "",
            "per_question": [],
            "overall_rating": {"rating": "", "explanation": ""},
            "timing": {},  # Add timing information
        }

        if not qa_results:
            return {
                "overall_analysis": "No QA results found to analyze",
                "strengths": [],
                "weaknesses": ["No data available for analysis"],
                "recommendations": ["Ensure input data contains QA results"],
                "use_case_fit": "Unable to determine",
                "per_question": [],
                "overall_rating": {
                    "rating": "error",
                    "explanation": "No QA results found",
                },
            }

        try:
            per_question_timings = []  # Track timing for each question

            # Set up intermediate output directory for crash recovery
            intermediate_dir = None
            experiment_name = results.get("metadata", {}).get(
                "experiment_name", "qa_evaluation"
            )
            if hasattr(self, "intermediate_dir") and self.intermediate_dir:
                # Use existing intermediate directory if set
                intermediate_dir = (
                    Path(self.intermediate_dir)
                    / f"{experiment_name}_qa_analysis.intermediate"
                )
            else:
                # Create in temp directory
                import tempfile

                temp_dir = Path(tempfile.gettempdir()) / "gaia_eval"
                intermediate_dir = (
                    temp_dir / f"{experiment_name}_qa_analysis.intermediate"
                )

            if intermediate_dir:
                intermediate_dir.mkdir(parents=True, exist_ok=True)
                self.log.info(
                    f"Writing intermediate QA analysis results to: {intermediate_dir}"
                )

            for qa_result in qa_results:
                question_start_time = time.time()

                # Calculate similarity score between ground truth and response
                similarity_score = self.calculate_similarity(
                    qa_result["ground_truth"], qa_result["response"]
                )

                # Store initial data (pass/fail will be determined after Claude analysis)
                threshold = results["metadata"]["similarity_threshold"]

                # Restructure the qa_result into qa_inputs
                qa_inputs = {
                    "query": qa_result["query"],
                    "ground_truth": qa_result["ground_truth"],
                    "response": qa_result["response"],
                    "similarity": similarity_score,
                    "threshold": threshold,
                }

                prompt = f"""
                    Analyze this RAG (Retrieval Augmented Generation) system test result and provide detailed insights.

                    Query: {qa_inputs['query']}
                    Ground Truth: {qa_inputs['ground_truth']}
                    System Response: {qa_inputs['response']}
                    Similarity Score: {qa_inputs['similarity']}

                    Evaluate the response on these criteria, providing both a rating (excellent/good/fair/poor) and detailed explanation:
                    1. Correctness: Is it factually correct compared to ground truth?
                    2. Completeness: Does it fully answer the question?
                    3. Conciseness: Is it appropriately brief while maintaining accuracy?
                    4. Relevance: Does it directly address the query?

                    Return your analysis in this exact JSON format:
                    {{
                        "correctness": {{
                            "rating": "one of: excellent/good/fair/poor",
                            "explanation": "analysis of factual correctness"
                        }},
                        "completeness": {{
                            "rating": "one of: excellent/good/fair/poor",
                            "explanation": "analysis of answer completeness"
                        }},
                        "conciseness": {{
                            "rating": "one of: excellent/good/fair/poor",
                            "explanation": "analysis of brevity and clarity"
                        }},
                        "relevance": {{
                            "rating": "one of: excellent/good/fair/poor",
                            "explanation": "analysis of how well it addresses the query"
                        }}
                    }}
                    """

                response_data = self.claude.get_completion_with_usage(prompt)

                try:
                    # Extract JSON and combine with qa_inputs
                    response = response_data["content"]
                    usage = response_data["usage"]
                    cost = response_data["cost"]

                    if isinstance(response, list):
                        response_text = (
                            response[0].text
                            if hasattr(response[0], "text")
                            else str(response[0])
                        )
                    else:
                        response_text = (
                            response.text
                            if hasattr(response, "text")
                            else str(response)
                        )

                    json_start = response_text.find("{")
                    json_end = response_text.rfind("}") + 1
                    if json_start >= 0 and json_end > json_start:
                        json_content = response_text[json_start:json_end]
                        qa_analysis = json.loads(json_content)

                        # Determine comprehensive pass/fail
                        pass_fail_result = self.determine_pass_fail(
                            similarity_score, threshold, qa_analysis
                        )

                        # Add all data to qa_inputs
                        qa_inputs.update(pass_fail_result)

                        # Add qa_inputs, usage, and cost as nested dictionaries
                        qa_analysis["qa_inputs"] = qa_inputs
                        qa_analysis["usage"] = usage
                        qa_analysis["cost"] = cost

                        # Add timing for this question
                        question_time = time.time() - question_start_time
                        qa_analysis["processing_time_seconds"] = round(question_time, 3)
                        per_question_timings.append(question_time)

                        analysis["per_question"].append(qa_analysis)

                        # Write intermediate result immediately for crash recovery
                        if intermediate_dir:
                            try:
                                intermediate_file = (
                                    intermediate_dir
                                    / f"qa_{len(analysis['per_question']):04d}_analysis.json"
                                )
                                intermediate_data = {
                                    "question_index": len(analysis["per_question"]) - 1,
                                    "experiment_name": experiment_name,
                                    "qa_inputs": qa_inputs,
                                    "analysis": qa_analysis,
                                    "usage": qa_analysis.get("usage", {}),
                                    "cost": qa_analysis.get("cost", {}),
                                    "processing_time_seconds": qa_analysis.get(
                                        "processing_time_seconds", 0
                                    ),
                                    "timestamp": datetime.now().isoformat(),
                                }

                                with open(
                                    intermediate_file, "w", encoding="utf-8"
                                ) as f:
                                    json.dump(intermediate_data, f, indent=2)

                                # Update progress file
                                progress_file = (
                                    intermediate_dir / "qa_analysis_progress.json"
                                )
                                progress_data = {
                                    "experiment_name": experiment_name,
                                    "total_questions": len(qa_results),
                                    "completed_questions": len(
                                        analysis["per_question"]
                                    ),
                                    "progress_percent": round(
                                        len(analysis["per_question"])
                                        / len(qa_results)
                                        * 100,
                                        1,
                                    ),
                                    "last_updated": datetime.now().isoformat(),
                                    "estimated_remaining_time": None,
                                }

                                # Calculate estimated remaining time
                                if len(per_question_timings) > 0:
                                    avg_time_per_question = sum(
                                        per_question_timings
                                    ) / len(per_question_timings)
                                    remaining_questions = len(qa_results) - len(
                                        analysis["per_question"]
                                    )
                                    estimated_remaining = (
                                        remaining_questions * avg_time_per_question
                                    )
                                    progress_data["estimated_remaining_time"] = round(
                                        estimated_remaining, 1
                                    )

                                with open(progress_file, "w", encoding="utf-8") as f:
                                    json.dump(progress_data, f, indent=2)

                                self.log.info(
                                    f"QA analysis progress: {len(analysis['per_question'])}/{len(qa_results)} questions completed ({progress_data['progress_percent']}%)"
                                )

                            except Exception as e:
                                self.log.warning(
                                    f"Failed to write intermediate QA analysis result {len(analysis['per_question'])}: {e}"
                                )

                    else:
                        self.log.error(f"No JSON found in response for question")

                        # Determine pass/fail without Claude analysis (similarity only)
                        pass_fail_result = self.determine_pass_fail(
                            similarity_score, threshold, None
                        )
                        qa_inputs.update(pass_fail_result)

                        # Add timing even for failed parsing
                        question_time = time.time() - question_start_time
                        per_question_timings.append(question_time)

                        analysis["per_question"].append(
                            {
                                "error": "Failed to parse analysis",
                                "raw_response": response_text,
                                "qa_inputs": qa_inputs,
                                "usage": usage,
                                "cost": cost,
                                "processing_time_seconds": round(question_time, 3),
                            }
                        )
                except Exception as e:
                    self.log.error(f"Error processing analysis: {e}")

                    # Determine pass/fail without Claude analysis (similarity only)
                    pass_fail_result = self.determine_pass_fail(
                        similarity_score, threshold, None
                    )
                    qa_inputs.update(pass_fail_result)

                    # Add timing even for exceptions
                    question_time = time.time() - question_start_time
                    per_question_timings.append(question_time)

                    analysis["per_question"].append(
                        {
                            "error": str(e),
                            "raw_response": str(response_data),
                            "qa_inputs": qa_inputs,
                            "usage": response_data.get("usage", {}),
                            "cost": response_data.get("cost", {}),
                            "processing_time_seconds": round(question_time, 3),
                        }
                    )

                    # Calculate similarity scores and accuracy metrics (extract from per_question analysis)
            calculated_similarities = [
                q["qa_inputs"]["similarity"]
                for q in analysis["per_question"]
                if "qa_inputs" in q
            ]
            pass_results = [
                q["qa_inputs"]["is_pass"]
                for q in analysis["per_question"]
                if "qa_inputs" in q
            ]

            # Calculate accuracy metrics
            total_questions = len(pass_results)
            passed_questions = sum(pass_results)
            failed_questions = total_questions - passed_questions
            accuracy = (
                passed_questions / total_questions if total_questions > 0 else 0.0
            )

            # After analyzing all questions, get overall analysis
            overall_start_time = time.time()
            overall_prompt = f"""
                Review these RAG system test results and provide an overall analysis.

                Number of questions: {total_questions}
                Similarity threshold: {results["metadata"]["similarity_threshold"]}
                Number passed threshold: {passed_questions}
                Number failed threshold: {failed_questions}
                Pass rate: {accuracy:.3f}
                Accuracy: {accuracy * 100:.1f}%

                Similarity statistics:
                - Mean: {np.mean(calculated_similarities):.3f}
                - Median: {np.median(calculated_similarities):.3f}
                - Min: {np.min(calculated_similarities):.3f}
                - Max: {np.max(calculated_similarities):.3f}
                - Standard Deviation: {np.std(calculated_similarities):.3f}

                Individual analyses: {json.dumps(analysis['per_question'], indent=2)}

                Provide a comprehensive analysis including:
                1. Overall Rating: Rate the system (excellent/good/fair/poor) with explanation
                2. Overall Analysis: General assessment of the RAG system's performance
                3. Strengths: What the system does well
                4. Weaknesses: Areas needing improvement
                5. Recommendations: Specific suggestions for improvement
                6. Use Case Fit: Types of queries the system handles well/poorly

                Return your analysis in this exact JSON format:
                {{
                    "overall_rating": {{
                        "rating": "one of: excellent/good/fair/poor",
                        "explanation": "explanation of the rating",
                        "metrics": {{
                            "num_questions": number of questions analyzed,
                            "similarity_threshold": threshold value used,
                            "num_passed": number of questions that passed threshold,
                            "num_failed": number of questions that failed threshold,
                            "pass_rate": pass rate as decimal,
                            "accuracy": accuracy as decimal,
                            "accuracy_percentage": accuracy as percentage,
                            "mean_similarity": average similarity score,
                            "median_similarity": median similarity score,
                            "min_similarity": minimum similarity score,
                            "max_similarity": maximum similarity score,
                            "std_similarity": standard deviation of similarity scores
                        }}
                    }},
                    "overall_analysis": "general assessment",
                    "strengths": ["strength 1", "strength 2", ...],
                    "weaknesses": ["weakness 1", "weakness 2", ...],
                    "recommendations": ["recommendation 1", "recommendation 2", ...],
                    "use_case_fit": "analysis of suitable use cases"
                }}
                """

            overall_response_data = self.claude.get_completion_with_usage(
                overall_prompt
            )

            try:
                # Extract JSON from overall response
                overall_response = overall_response_data["content"]
                overall_usage = overall_response_data["usage"]
                overall_cost = overall_response_data["cost"]

                if isinstance(overall_response, list):
                    response_text = (
                        overall_response[0].text
                        if hasattr(overall_response[0], "text")
                        else str(overall_response[0])
                    )
                else:
                    response_text = (
                        overall_response.text
                        if hasattr(overall_response, "text")
                        else str(overall_response)
                    )

                json_start = response_text.find("{")
                json_end = response_text.rfind("}") + 1
                if json_start >= 0 and json_end > json_start:
                    json_content = response_text[json_start:json_end]
                    overall_analysis = json.loads(json_content)
                    # Add overall usage and cost to the analysis
                    overall_analysis["overall_usage"] = overall_usage
                    overall_analysis["overall_cost"] = overall_cost

                    # Add overall timing
                    overall_time = time.time() - overall_start_time
                    overall_analysis["overall_processing_time_seconds"] = round(
                        overall_time, 3
                    )

                    analysis.update(overall_analysis)
                else:
                    self.log.error("No JSON found in overall analysis response")
                    analysis.update(
                        {
                            "error": "Failed to parse overall analysis",
                            "raw_response": response_text,
                            "overall_usage": overall_usage,
                            "overall_cost": overall_cost,
                        }
                    )
            except Exception as e:
                self.log.error(f"Error processing overall analysis: {e}")
                analysis.update(
                    {
                        "error": str(e),
                        "raw_response": str(overall_response_data),
                        "overall_usage": overall_response_data.get("usage", {}),
                        "overall_cost": overall_response_data.get("cost", {}),
                    }
                )

            # Calculate total cost across all questions and overall analysis
            total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            total_cost = {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0}

            # Sum up costs from per-question analysis
            for question_analysis in analysis["per_question"]:
                if "usage" in question_analysis and "cost" in question_analysis:
                    usage = question_analysis["usage"]
                    cost = question_analysis["cost"]
                    total_usage["input_tokens"] += usage.get("input_tokens", 0)
                    total_usage["output_tokens"] += usage.get("output_tokens", 0)
                    total_usage["total_tokens"] += usage.get("total_tokens", 0)
                    total_cost["input_cost"] += cost.get("input_cost", 0.0)
                    total_cost["output_cost"] += cost.get("output_cost", 0.0)
                    total_cost["total_cost"] += cost.get("total_cost", 0.0)

            # Add overall analysis costs if available
            if "overall_usage" in analysis and "overall_cost" in analysis:
                overall_usage = analysis["overall_usage"]
                overall_cost = analysis["overall_cost"]
                total_usage["input_tokens"] += overall_usage.get("input_tokens", 0)
                total_usage["output_tokens"] += overall_usage.get("output_tokens", 0)
                total_usage["total_tokens"] += overall_usage.get("total_tokens", 0)
                total_cost["input_cost"] += overall_cost.get("input_cost", 0.0)
                total_cost["output_cost"] += overall_cost.get("output_cost", 0.0)
                total_cost["total_cost"] += overall_cost.get("total_cost", 0.0)

            # Add total cost summary to analysis
            analysis["total_usage"] = total_usage
            analysis["total_cost"] = total_cost

            # Add comprehensive timing information
            total_time = time.time() - analysis_start_time
            analysis["timing"] = {
                "total_processing_time_seconds": round(total_time, 3),
                "per_question_times_seconds": [
                    round(t, 3) for t in per_question_timings
                ],
                "average_per_question_seconds": (
                    round(np.mean(per_question_timings), 3)
                    if per_question_timings
                    else 0
                ),
                "max_per_question_seconds": (
                    round(max(per_question_timings), 3) if per_question_timings else 0
                ),
                "min_per_question_seconds": (
                    round(min(per_question_timings), 3) if per_question_timings else 0
                ),
            }

            # Clean up intermediate files after successful completion
            if intermediate_dir and intermediate_dir.exists():
                try:
                    import shutil

                    shutil.rmtree(intermediate_dir)
                    self.log.info(
                        f"Cleaned up intermediate QA analysis files from: {intermediate_dir}"
                    )
                except Exception as e:
                    self.log.warning(
                        f"Failed to clean up intermediate directory {intermediate_dir}: {e}"
                    )

            return analysis
        except Exception as api_error:
            if "529" in str(api_error) or "overloaded" in str(api_error).lower():
                self.log.warning(
                    "Claude API is currently overloaded. Returning partial analysis with raw data."
                )
                # Include raw QA results without Claude analysis
                for qa_result in qa_results:
                    # Calculate similarity score even when Claude analysis fails
                    similarity_score = self.calculate_similarity(
                        qa_result["ground_truth"], qa_result["response"]
                    )

                    # Determine pass/fail without Claude analysis (similarity only)
                    threshold = results["metadata"]["similarity_threshold"]

                    qa_inputs = {
                        "query": qa_result["query"],
                        "ground_truth": qa_result["ground_truth"],
                        "response": qa_result["response"],
                        "similarity": similarity_score,
                        "threshold": threshold,
                    }

                    # Add pass/fail determination
                    pass_fail_result = self.determine_pass_fail(
                        similarity_score, threshold, None
                    )
                    qa_inputs.update(pass_fail_result)
                    analysis["per_question"].append(
                        {
                            "status": "raw_data_only",
                            "analysis_error": "Claude API overloaded",
                            "qa_inputs": qa_inputs,
                        }
                    )

                analysis.update(
                    {
                        "overall_analysis": "Analysis incomplete due to Claude API overload",
                        "strengths": ["Raw data preserved"],
                        "weaknesses": [
                            "Claude analysis unavailable due to API overload"
                        ],
                        "recommendations": ["Retry analysis when API is available"],
                        "use_case_fit": "Analysis pending",
                        "overall_rating": {
                            "rating": "pending",
                            "explanation": "Claude API temporarily unavailable",
                        },
                    }
                )
                return analysis
            raise  # Re-raise if it's not an overload error

        except Exception as e:
            self.log.error(f"Error in analyze_with_claude: {e}")
            return {
                "overall_analysis": f"Analysis failed: {str(e)}",
                "strengths": [],
                "weaknesses": ["Analysis failed to complete"],
                "recommendations": ["Check logs for error details"],
                "use_case_fit": "",
                "per_question": [],
                "overall_rating": {"rating": "error", "explanation": str(e)},
            }

    def _analyze_summarization_results(
        self,
        results: Dict,
        summarization_results: List,
        groundtruth_path: Optional[str] = None,
    ) -> Dict:
        """Analyze summarization results using Claude."""
        # Start timing
        analysis_start_time = time.time()

        analysis = {
            "overall_analysis": "",
            "strengths": [],
            "weaknesses": [],
            "recommendations": [],
            "use_case_fit": "",
            "per_question": [],
            "overall_rating": {"rating": "", "explanation": ""},
            "timing": {},  # Add timing information
        }

        if not summarization_results:
            return {
                "overall_analysis": "No summarization results found to analyze",
                "strengths": [],
                "weaknesses": ["No summarization data available for analysis"],
                "recommendations": ["Ensure input data contains summarization results"],
                "use_case_fit": "Unable to determine",
                "per_question": [],
                "overall_rating": {
                    "rating": "error",
                    "explanation": "No summarization results found",
                },
            }

        try:
            # Load ground truth summaries from separate file if provided
            ground_truth_data = None
            if groundtruth_path and Path(groundtruth_path).exists():
                try:
                    with open(groundtruth_path, "r", encoding="utf-8") as f:
                        ground_truth_data = json.load(f)
                    self.log.info(f"Loaded ground truth data from: {groundtruth_path}")

                    # Check if this is a consolidated ground truth file
                    if "consolidated_from" in ground_truth_data.get("metadata", {}):
                        self.log.info(
                            f"Using consolidated ground truth with {ground_truth_data['metadata']['consolidated_from']} transcripts"
                        )

                except Exception as e:
                    self.log.warning(
                        f"Failed to load ground truth file {groundtruth_path}: {e}"
                    )
                    ground_truth_data = None
            elif groundtruth_path:
                self.log.warning(f"Ground truth file not found: {groundtruth_path}")

            total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            total_cost = {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0}
            per_summary_timings = []  # Track timing for each summary

            # Set up intermediate output directory for crash recovery
            intermediate_dir = None
            experiment_name = results.get("metadata", {}).get(
                "experiment_name", "evaluation"
            )
            if hasattr(self, "intermediate_dir") and self.intermediate_dir:
                # Use existing intermediate directory if set
                intermediate_dir = (
                    Path(self.intermediate_dir)
                    / f"{experiment_name}_analysis.intermediate"
                )
            else:
                # Create in temp directory
                import tempfile

                temp_dir = Path(tempfile.gettempdir()) / "gaia_eval"
                intermediate_dir = temp_dir / f"{experiment_name}_analysis.intermediate"

            if intermediate_dir:
                intermediate_dir.mkdir(parents=True, exist_ok=True)
                self.log.info(
                    f"Writing intermediate analysis results to: {intermediate_dir}"
                )

            for i, summary_result in enumerate(summarization_results):
                summary_start_time = time.time()
                generated_summaries = summary_result.get("generated_summaries", {})

                # Get ground truth summaries from embedded data or separate file
                groundtruth_summaries = summary_result.get("groundtruth_summaries", {})

                # If no embedded ground truth but we have a ground truth file, extract from it
                if not groundtruth_summaries and ground_truth_data:
                    gt_analysis = ground_truth_data.get("analysis", {})
                    gt_summaries = gt_analysis.get("summaries", {})

                    # Handle both regular and consolidated ground truth formats
                    if gt_summaries:
                        # Check if this is consolidated format (summaries have transcript_id keys)
                        if "consolidated_from" in ground_truth_data.get("metadata", {}):
                            # For consolidated format, try to match by source file or use first available
                            source_file = summary_result.get("source_file", "")
                            transcript_id = None

                            # Try to match by source file name using metadata.source_files
                            source_files = ground_truth_data.get("metadata", {}).get(
                                "source_files", []
                            )
                            for source_mapping in source_files:
                                mapped_source = source_mapping.get("source_file", "")
                                if source_file and (
                                    source_file == mapped_source
                                    or source_file.replace("/", "\\") == mapped_source
                                    or source_file.replace("\\", "/") == mapped_source
                                ):
                                    transcript_id = source_mapping.get("transcript_id")
                                    break

                            # If no match found, use first available transcript
                            if not transcript_id and gt_summaries:
                                transcript_id = list(gt_summaries.keys())[0]
                                self.log.warning(
                                    f"No exact match found for source {source_file}, using {transcript_id}"
                                )

                            if transcript_id and transcript_id in gt_summaries:
                                groundtruth_summaries = gt_summaries[transcript_id]
                                self.log.debug(
                                    f"Using consolidated ground truth summaries for {transcript_id}"
                                )
                        else:
                            # Regular format - summaries are directly under gt_summaries
                            groundtruth_summaries = gt_summaries
                            self.log.debug(
                                f"Using regular ground truth summaries from file for summary {i}"
                            )

                # Analyze each summary component
                summary_analysis = {
                    "summary_index": i,
                    "source_file": summary_result.get("source_file", ""),
                    "analysis": {},
                    "overall_quality": "",
                }

                # Compare generated vs ground truth if available
                if groundtruth_summaries:
                    prompt = f"""
                    Analyze this summarization system result by comparing the generated summary against the ground truth.

                    GENERATED SUMMARY:
                    Executive Summary: {generated_summaries.get('executive_summary', 'N/A')}
                    Detailed Summary: {generated_summaries.get('detailed_summary', 'N/A')}
                    Action Items: {generated_summaries.get('action_items', [])}
                    Key Decisions: {generated_summaries.get('key_decisions', [])}
                    Participants: {generated_summaries.get('participants', [])}
                    Topics Discussed: {generated_summaries.get('topics_discussed', [])}

                    GROUND TRUTH SUMMARY:
                    Executive Summary: {groundtruth_summaries.get('executive_summary', 'N/A')}
Detailed Summary: {groundtruth_summaries.get('detailed_summary', 'N/A')}
Action Items: {groundtruth_summaries.get('action_items', [])}
Key Decisions: {groundtruth_summaries.get('key_decisions', [])}
Participants: {groundtruth_summaries.get('participants', [])}
Topics Discussed: {groundtruth_summaries.get('topics_discussed', [])}

                    Evaluate the generated summary on these criteria (rate each as excellent/good/fair/poor):
                    1. Executive Summary Accuracy: How well does the executive summary capture the key points?
                    2. Completeness: Are all important details covered?
                    3. Action Items Accuracy: Are action items correctly identified and detailed?
                    4. Key Decisions Accuracy: Are key decisions properly captured?
                    5. Participant Identification: Are participants correctly identified?
                    6. Topic Coverage: Are all discussed topics included?

                    Return your analysis in this JSON format:
                    {{
                        "executive_summary_quality": {{
                            "rating": "excellent/good/fair/poor",
                            "explanation": "detailed analysis"
                        }},
                        "detail_completeness": {{
                            "rating": "excellent/good/fair/poor", 
                            "explanation": "detailed analysis"
                        }},
                        "action_items_structure": {{
                            "rating": "excellent/good/fair/poor",
                            "explanation": "detailed analysis"
                        }},
                        "key_decisions_clarity": {{
                            "rating": "excellent/good/fair/poor",
                            "explanation": "detailed analysis"
                        }},
                        "participant_information": {{
                            "rating": "excellent/good/fair/poor",
                            "explanation": "detailed analysis"
                        }},
                        "topic_organization": {{
                            "rating": "excellent/good/fair/poor",
                            "explanation": "detailed analysis"
                        }},
                        "overall_quality": "excellent/good/fair/poor"
                    }}
                    """
                else:
                    # Analyze standalone summary quality
                    prompt = f"""
                    Analyze this generated meeting summary for quality and completeness.

                    GENERATED SUMMARY:
                    Executive Summary: {generated_summaries.get('executive_summary', 'N/A')}
                    Detailed Summary: {generated_summaries.get('detailed_summary', 'N/A')}
                    Action Items: {generated_summaries.get('action_items', [])}
                    Key Decisions: {generated_summaries.get('key_decisions', [])}
                    Participants: {generated_summaries.get('participants', [])}
                    Topics Discussed: {generated_summaries.get('topics_discussed', [])}

                    Evaluate the summary quality (rate each as excellent/good/fair/poor):
                    1. Executive Summary Quality: Is it clear and high-level?
                    2. Detail Completeness: Does the detailed summary provide sufficient context?
                    3. Action Items Structure: Are action items specific and actionable?
                    4. Key Decisions Clarity: Are decisions clearly stated?
                    5. Participant Information: Are participants properly identified?
                    6. Topic Organization: Are topics well-organized and comprehensive?

                    IMPORTANT: Return ONLY valid JSON with no additional text, markdown formatting, or explanations.
                    Ensure all JSON syntax is correct - no trailing commas, proper quotes, and complete structure.

                    Return your analysis in this exact JSON format:
                    {{
                        "executive_summary_quality": {{
                            "rating": "excellent/good/fair/poor",
                            "explanation": "detailed analysis"
                        }},
                        "detail_completeness": {{
                            "rating": "excellent/good/fair/poor",
                            "explanation": "detailed analysis"
                        }},
                        "action_items_structure": {{
                            "rating": "excellent/good/fair/poor",
                            "explanation": "detailed analysis"
                        }},
                        "key_decisions_clarity": {{
                            "rating": "excellent/good/fair/poor",
                            "explanation": "detailed analysis"
                        }},
                        "participant_information": {{
                            "rating": "excellent/good/fair/poor",
                            "explanation": "detailed analysis"
                        }},
                        "topic_organization": {{
                            "rating": "excellent/good/fair/poor",
                            "explanation": "detailed analysis"
                        }},
                        "overall_quality": "excellent/good/fair/poor"
                    }}
                    """

                try:
                    response_data = self.claude.get_completion_with_usage(prompt)
                    response = response_data["content"]
                    usage = response_data["usage"]
                    cost = response_data["cost"]

                    # Extract text from response
                    if isinstance(response, list):
                        response_text = (
                            response[0].text
                            if hasattr(response[0], "text")
                            else str(response[0])
                        )
                    else:
                        response_text = (
                            response.text
                            if hasattr(response, "text")
                            else str(response)
                        )

                    # Parse JSON response with improved error handling
                    # First try to extract from markdown code blocks
                    markdown_json = re.search(
                        r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL
                    )
                    if markdown_json:
                        json_content = markdown_json.group(1)
                    else:
                        # Fall back to finding raw JSON
                        json_start = response_text.find("{")
                        json_end = response_text.rfind("}") + 1
                        if json_start >= 0 and json_end > json_start:
                            json_content = response_text[json_start:json_end]
                        else:
                            json_content = None

                    if json_content:
                        try:
                            # First attempt: direct JSON parsing
                            summary_analysis["analysis"] = json.loads(json_content)
                        except json.JSONDecodeError as e:
                            self.log.warning(f"Initial JSON parse failed: {e}")
                            # Second attempt: clean up common issues
                            # Remove any trailing commas before closing braces/brackets
                            cleaned_json = re.sub(r",\s*([}\]])", r"\1", json_content)
                            # Replace single quotes with double quotes (if any) - but not within strings
                            # This is a simple heuristic, not perfect
                            cleaned_json = cleaned_json.replace("'", '"')
                            # Remove any control characters except newlines and tabs
                            cleaned_json = re.sub(
                                r"[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f-\x9f]",
                                "",
                                cleaned_json,
                            )
                            # Fix common escape issues
                            cleaned_json = cleaned_json.replace(
                                '\\"', '"'
                            )  # Remove escaped quotes that might be double-escaped
                            cleaned_json = re.sub(
                                r'(?<!\\)\\(?!["\\/bfnrt])', r"\\\\", cleaned_json
                            )  # Fix unescaped backslashes

                            try:
                                summary_analysis["analysis"] = json.loads(cleaned_json)
                                self.log.info("Successfully parsed JSON after cleanup")
                            except json.JSONDecodeError as e2:
                                self.log.error(f"JSON parse failed after cleanup: {e2}")
                                # Third attempt: extract individual fields manually
                                analysis_dict = {}

                                # Try to extract each field individually
                                fields = [
                                    "executive_summary_quality",
                                    "detail_completeness",
                                    "action_items_structure",
                                    "key_decisions_clarity",
                                    "participant_information",
                                    "topic_organization",
                                    "overall_quality",
                                ]

                                for field in fields:
                                    # Find the field and extract its rating
                                    pattern = rf'"{field}":\s*(?:"([^"]+)"|{{[^}}]+}})'
                                    match = re.search(pattern, json_content)
                                    if match:
                                        if field == "overall_quality":
                                            analysis_dict[field] = (
                                                match.group(1)
                                                if match.group(1)
                                                else "unknown"
                                            )
                                        else:
                                            # Try to extract rating from nested object
                                            rating_pattern = rf'"{field}":\s*{{[^}}]*"rating":\s*"([^"]+)"'
                                            rating_match = re.search(
                                                rating_pattern, json_content
                                            )
                                            if rating_match:
                                                analysis_dict[field] = {
                                                    "rating": rating_match.group(1),
                                                    "explanation": "Extracted from partial JSON",
                                                }

                                if analysis_dict:
                                    summary_analysis["analysis"] = analysis_dict
                                    self.log.warning(
                                        f"PARTIAL RECOVERY - Used fallback field extraction for summary {i}, extracted {len(analysis_dict)} fields"
                                    )
                                else:
                                    # Final fallback: save raw response for debugging
                                    self.log.error(
                                        f"FALLBACK VALUES USED - Complete JSON parse failure for summary {i}"
                                    )
                                    summary_analysis["analysis"] = {
                                        "error": f"[FALLBACK - JSON PARSE FAILED] {str(e2)}",
                                        "raw_response": response_text[
                                            :1000
                                        ],  # First 1000 chars for debugging
                                        "_warning": "This is a fallback response - Claude's analysis could not be parsed",
                                        "executive_summary_quality": {
                                            "rating": "error",
                                            "explanation": "[PARSE ERROR - See raw_response]",
                                        },
                                        "detail_completeness": {
                                            "rating": "error",
                                            "explanation": "[PARSE ERROR - See raw_response]",
                                        },
                                        "action_items_structure": {
                                            "rating": "error",
                                            "explanation": "[PARSE ERROR - See raw_response]",
                                        },
                                        "key_decisions_clarity": {
                                            "rating": "error",
                                            "explanation": "[PARSE ERROR - See raw_response]",
                                        },
                                        "participant_information": {
                                            "rating": "error",
                                            "explanation": "[PARSE ERROR - See raw_response]",
                                        },
                                        "topic_organization": {
                                            "rating": "error",
                                            "explanation": "[PARSE ERROR - See raw_response]",
                                        },
                                    }
                                    summary_analysis["overall_quality"] = "error"

                        # Set overall quality if successfully parsed
                        if "analysis" in summary_analysis and isinstance(
                            summary_analysis["analysis"], dict
                        ):
                            summary_analysis["overall_quality"] = summary_analysis[
                                "analysis"
                            ].get("overall_quality", "unknown")
                        else:
                            summary_analysis["overall_quality"] = "error"
                    else:
                        summary_analysis["analysis"] = {
                            "error": "No JSON content found in Claude response",
                            "raw_response": response_text[:500],
                        }
                        summary_analysis["overall_quality"] = "error"

                    # Add usage and cost
                    summary_analysis["usage"] = usage
                    summary_analysis["cost"] = cost

                    # Add timing for this summary
                    summary_time = time.time() - summary_start_time
                    summary_analysis["processing_time_seconds"] = round(summary_time, 3)
                    per_summary_timings.append(summary_time)

                    # Accumulate totals
                    total_usage["input_tokens"] += usage.get("input_tokens", 0)
                    total_usage["output_tokens"] += usage.get("output_tokens", 0)
                    total_usage["total_tokens"] += usage.get("total_tokens", 0)
                    total_cost["input_cost"] += cost.get("input_cost", 0.0)
                    total_cost["output_cost"] += cost.get("output_cost", 0.0)
                    total_cost["total_cost"] += cost.get("total_cost", 0.0)

                except Exception as e:
                    self.log.error(f"Error analyzing summary {i}: {e}")
                    summary_analysis["analysis"] = {"error": str(e)}
                    summary_analysis["overall_quality"] = "error"

                    # Add timing even for errors
                    summary_time = time.time() - summary_start_time
                    summary_analysis["processing_time_seconds"] = round(summary_time, 3)
                    per_summary_timings.append(summary_time)

                analysis["per_question"].append(summary_analysis)

                # Write intermediate result immediately for crash recovery
                if intermediate_dir:
                    try:
                        intermediate_file = (
                            intermediate_dir / f"summary_{i+1:04d}_analysis.json"
                        )
                        intermediate_data = {
                            "summary_index": i,
                            "experiment_name": experiment_name,
                            "source_file": summary_result.get("source_file", ""),
                            "analysis": summary_analysis,
                            "usage": summary_analysis.get("usage", {}),
                            "cost": summary_analysis.get("cost", {}),
                            "processing_time_seconds": summary_analysis.get(
                                "processing_time_seconds", 0
                            ),
                            "timestamp": datetime.now().isoformat(),
                        }

                        with open(intermediate_file, "w", encoding="utf-8") as f:
                            json.dump(intermediate_data, f, indent=2)

                        # Update progress file
                        progress_file = intermediate_dir / "analysis_progress.json"
                        progress_data = {
                            "experiment_name": experiment_name,
                            "total_summaries": len(summarization_results),
                            "completed_summaries": i + 1,
                            "progress_percent": round(
                                (i + 1) / len(summarization_results) * 100, 1
                            ),
                            "total_usage": total_usage.copy(),
                            "total_cost": total_cost.copy(),
                            "last_updated": datetime.now().isoformat(),
                            "estimated_remaining_time": None,
                        }

                        # Calculate estimated remaining time
                        if i > 0:
                            avg_time_per_summary = sum(per_summary_timings) / len(
                                per_summary_timings
                            )
                            remaining_summaries = len(summarization_results) - (i + 1)
                            estimated_remaining = (
                                remaining_summaries * avg_time_per_summary
                            )
                            progress_data["estimated_remaining_time"] = round(
                                estimated_remaining, 1
                            )

                        with open(progress_file, "w", encoding="utf-8") as f:
                            json.dump(progress_data, f, indent=2)

                        self.log.info(
                            f"Analysis progress: {i+1}/{len(summarization_results)} summaries completed ({progress_data['progress_percent']}%)"
                        )

                    except Exception as e:
                        self.log.warning(
                            f"Failed to write intermediate analysis result {i+1}: {e}"
                        )

            # Generate overall analysis
            quality_ratings = [
                s.get("overall_quality", "unknown") for s in analysis["per_question"]
            ]

            # Filter out error and unknown ratings for scoring
            valid_quality_ratings = [
                rating
                for rating in quality_ratings
                if rating in ["excellent", "good", "fair", "poor"]
            ]

            excellent_count = valid_quality_ratings.count("excellent")
            good_count = valid_quality_ratings.count("good")
            fair_count = valid_quality_ratings.count("fair")
            poor_count = valid_quality_ratings.count("poor")
            total_summaries = len(valid_quality_ratings)
            error_count = quality_ratings.count("error")

            # Log information about errors if any
            if error_count > 0:
                self.log.warning(
                    f"Excluded {error_count} error entries from quality scoring"
                )

            # Handle case where no valid summaries are available for scoring
            if total_summaries == 0:
                if error_count > 0:
                    self.log.error(
                        "All summaries failed analysis - cannot compute quality score"
                    )
                    overall_rating = "error"
                else:
                    self.log.warning("No summaries found for analysis")
                    overall_rating = "unknown"
            elif excellent_count >= total_summaries * 0.7:
                overall_rating = "excellent"
            elif (excellent_count + good_count) >= total_summaries * 0.7:
                overall_rating = "good"
            elif (excellent_count + good_count + fair_count) >= total_summaries * 0.7:
                overall_rating = "fair"
            else:
                overall_rating = "poor"

            # Send individual analyses to Claude for comprehensive overall analysis
            overall_start_time = time.time()

            # Get experiment/model information
            experiment_name = results.get("metadata", {}).get(
                "experiment_name", "Unknown Model"
            )
            model_type = results.get("metadata", {}).get("model", "")

            overall_prompt = f"""
                Review these summarization test results and provide a comprehensive overall analysis.

                Model/Experiment: {experiment_name}
                Number of summaries analyzed: {total_summaries}
                Quality distribution:
                - Excellent: {excellent_count} ({excellent_count/total_summaries*100:.1f}%)
                - Good: {good_count} ({good_count/total_summaries*100:.1f}%)
                - Fair: {fair_count} ({fair_count/total_summaries*100:.1f}%)
                - Poor: {poor_count} ({poor_count/total_summaries*100:.1f}%)
                
                Overall quality rating: {overall_rating}

                Individual summary analyses: {json.dumps(analysis['per_question'], indent=2)}

                Based on the detailed analysis of each summary above, provide a comprehensive assessment including:
                
                1. Overall Analysis: General assessment of the summarization system's performance
                2. Strengths: Specific aspects the model does well (be specific based on the individual analyses)
                3. Weaknesses: Concrete areas needing improvement (based on patterns in the individual analyses)
                4. Recommendations: Actionable suggestions for improvement
                5. Use Case Fit: Types of meetings/content this model handles well or poorly
                
                Consider the following in your analysis:
                - Patterns in accuracy, completeness, organization across summaries
                - Consistency of performance
                - Specific failure modes observed
                - Model characteristics (e.g., if it's Claude, Llama, Qwen, etc.)
                
                IMPORTANT: Return ONLY valid JSON with no additional text, markdown formatting, or explanations.
                Ensure all JSON syntax is correct - no trailing commas, proper quotes, and complete structure.

                Return your analysis in this exact JSON format:
                {{
                    "overall_analysis": "comprehensive assessment of overall performance",
                    "strengths": ["specific strength 1", "specific strength 2", ...],
                    "weaknesses": ["specific weakness 1", "specific weakness 2", ...],
                    "recommendations": ["actionable recommendation 1", "actionable recommendation 2", ...],
                    "use_case_fit": "detailed analysis of suitable use cases and limitations"
                }}
                """

            try:
                overall_response_data = self.claude.get_completion_with_usage(
                    overall_prompt
                )

                # Extract JSON from overall response
                overall_response = overall_response_data["content"]
                overall_usage = overall_response_data["usage"]
                overall_cost = overall_response_data["cost"]

                if isinstance(overall_response, list):
                    response_text = (
                        overall_response[0].text
                        if hasattr(overall_response[0], "text")
                        else str(overall_response[0])
                    )
                else:
                    response_text = (
                        overall_response.text
                        if hasattr(overall_response, "text")
                        else str(overall_response)
                    )

                # Try to extract JSON from various formats (markdown, plain, etc.)
                # First try to extract from markdown code blocks
                markdown_json = re.search(
                    r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL
                )
                if markdown_json:
                    json_content = markdown_json.group(1)
                    json_found = True
                else:
                    # Fall back to finding raw JSON
                    json_start = response_text.find("{")
                    json_end = response_text.rfind("}") + 1
                    if json_start >= 0 and json_end > json_start:
                        json_content = response_text[json_start:json_end]
                        json_found = True
                    else:
                        json_found = False

                if json_found:
                    try:
                        # First attempt: direct JSON parsing
                        claude_analysis = json.loads(json_content)
                    except json.JSONDecodeError as e:
                        self.log.warning(
                            f"Initial JSON parse failed for overall analysis: {e}"
                        )
                        # Second attempt: clean up common issues
                        # Remove trailing commas before closing braces/brackets
                        cleaned_json = re.sub(r",\s*([}\]])", r"\1", json_content)
                        # Replace single quotes with double quotes (if any) - simple heuristic
                        cleaned_json = cleaned_json.replace("'", '"')
                        # Remove control characters except newlines and tabs
                        cleaned_json = re.sub(
                            r"[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f-\x9f]", "", cleaned_json
                        )
                        # Fix common escape issues
                        cleaned_json = cleaned_json.replace(
                            '\\"', '"'
                        )  # Remove escaped quotes that might be double-escaped
                        cleaned_json = re.sub(
                            r'(?<!\\)\\(?!["\\/bfnrt])', r"\\\\", cleaned_json
                        )  # Fix unescaped backslashes

                        try:
                            claude_analysis = json.loads(cleaned_json)
                            self.log.info(
                                "Successfully parsed overall analysis JSON after cleanup"
                            )
                        except json.JSONDecodeError as e2:
                            self.log.error(
                                f"FALLBACK VALUES USED - Failed to parse Claude's overall analysis response after cleanup: {e2}"
                            )
                            self.log.error(
                                f"Raw response preview: {json_content[:500]}..."
                            )
                            # Use fallback values - CLEARLY MARKED
                            claude_analysis = {
                                "overall_analysis": f"[FALLBACK - JSON PARSE ERROR] Analyzed {total_summaries} summaries. Quality distribution: {excellent_count} excellent, {good_count} good, {fair_count} fair, {poor_count} poor. NOTE: Claude's detailed analysis could not be parsed due to malformed JSON response.",
                                "strengths": [
                                    "[FALLBACK VALUE - Real analysis unavailable due to JSON parse error]"
                                ],
                                "weaknesses": [
                                    "[FALLBACK VALUE - Real analysis unavailable due to JSON parse error]"
                                ],
                                "recommendations": [
                                    "[FALLBACK VALUE - Real analysis unavailable due to JSON parse error]",
                                    "Review raw Claude response in logs for actual recommendations",
                                ],
                                "use_case_fit": "[FALLBACK VALUE - Real analysis unavailable due to JSON parse error]",
                                "_warning": "These are fallback values - Claude's actual analysis failed to parse. Check logs for details.",
                            }

                    # Add Claude's analysis to our results
                    overall_analysis_text = claude_analysis.get(
                        "overall_analysis",
                        f"Analyzed {total_summaries} summaries. Quality distribution: {excellent_count} excellent, {good_count} good, {fair_count} fair, {poor_count} poor.",
                    )
                    strengths = claude_analysis.get(
                        "strengths", ["Summary generation completed"]
                    )
                    weaknesses = claude_analysis.get(
                        "weaknesses", ["Areas for improvement identified"]
                    )
                    recommendations = claude_analysis.get(
                        "recommendations", ["Continue monitoring performance"]
                    )
                    use_case_fit = claude_analysis.get(
                        "use_case_fit", "Suitable for meeting summarization"
                    )

                    # Track Claude API usage for overall analysis
                    analysis["overall_usage"] = overall_usage
                    analysis["overall_cost"] = overall_cost
                    analysis["overall_processing_time_seconds"] = round(
                        time.time() - overall_start_time, 3
                    )
                else:
                    self.log.error(
                        "FALLBACK VALUES USED - No JSON content found in Claude's overall analysis response"
                    )
                    self.log.error(
                        f"Raw response preview (first 1000 chars): {response_text[:1000]}"
                    )
                    # Save full response to debug file
                    debug_dir = Path("debug_claude_responses")
                    debug_dir.mkdir(exist_ok=True)
                    debug_file = (
                        debug_dir
                        / f"overall_analysis_no_json_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                    )
                    with open(debug_file, "w", encoding="utf-8") as f:
                        f.write(
                            f"No JSON found in response. JSON start: {json_start}, JSON end: {json_end}\n"
                        )
                        f.write(f"Full response:\n{response_text}")
                    self.log.error(f"Full response saved to: {debug_file}")
                    # Fallback to programmatic analysis - CLEARLY MARKED
                    overall_analysis_text = f"[FALLBACK - NO JSON FOUND] Analyzed {total_summaries} summaries. Quality distribution: {excellent_count} excellent, {good_count} good, {fair_count} fair, {poor_count} poor. NOTE: Claude's response contained no parseable JSON."
                    strengths = [
                        "[FALLBACK VALUE - Claude response had no JSON content]"
                    ]
                    weaknesses = [
                        "[FALLBACK VALUE - Manual review of logs required to see actual Claude response]"
                    ]
                    recommendations = [
                        "[FALLBACK VALUE - Check logs for Claude's actual response]"
                    ]
                    use_case_fit = "[FALLBACK VALUE - Claude's analysis not available]"

            except Exception as e:
                self.log.error(
                    f"FALLBACK VALUES USED - Exception during Claude overall analysis: {e}"
                )
                # Fallback to basic programmatic analysis if Claude fails - CLEARLY MARKED
                overall_analysis_text = f"[FALLBACK - EXCEPTION: {str(e)[:100]}] Analyzed {total_summaries} summaries. Quality distribution: {excellent_count} excellent, {good_count} good, {fair_count} fair, {poor_count} poor."
                strengths = [f"[FALLBACK VALUE - Claude API error: {str(e)[:100]}]"]
                weaknesses = ["[FALLBACK VALUE - Analysis failed due to API error]"]
                recommendations = [
                    "[FALLBACK VALUE - Check API connectivity and retry]"
                ]

                # Basic programmatic fallback analysis
                if excellent_count > 0:
                    strengths.append(
                        f"Achieved excellent quality in {excellent_count}/{total_summaries} summaries"
                    )
                if good_count > 0:
                    strengths.append(
                        f"Produced good quality summaries in {good_count}/{total_summaries} cases"
                    )

                if poor_count > 0:
                    weaknesses.append(
                        f"Generated poor quality summaries in {poor_count}/{total_summaries} cases"
                    )
                if excellent_count == 0:
                    weaknesses.append("No summaries achieved excellent quality rating")

                if poor_count > 0 or fair_count > total_summaries * 0.3:
                    recommendations.append("Review and improve prompt engineering")
                if excellent_count == 0:
                    recommendations.append("Consider using a more capable model")

                if not strengths:
                    strengths = ["Summary generation completed"]
                if not weaknesses:
                    weaknesses = ["Some areas for improvement"]
                if not recommendations:
                    recommendations = ["Continue monitoring performance"]

                use_case_fit = "Suitable for meeting summarization with review"

            analysis.update(
                {
                    "overall_analysis": overall_analysis_text,
                    "strengths": strengths,
                    "weaknesses": weaknesses,
                    "recommendations": recommendations,
                    "use_case_fit": use_case_fit,
                    "overall_rating": {
                        "rating": overall_rating,
                        "explanation": f"Based on {total_summaries} valid summaries with {excellent_count + good_count} high-quality results"
                        + (
                            f" ({error_count} errors excluded)"
                            if error_count > 0
                            else ""
                        ),
                        "metrics": {
                            "total_summaries": total_summaries,
                            "excellent_count": excellent_count,
                            "good_count": good_count,
                            "fair_count": fair_count,
                            "poor_count": poor_count,
                            "error_count": error_count,
                            "quality_score": (
                                (
                                    (
                                        excellent_count * 4
                                        + good_count * 3
                                        + fair_count * 2
                                        + poor_count * 1
                                    )
                                    / total_summaries
                                    - 1  # Convert from 1-4 scale to 0-3 scale
                                )
                                * 100
                                / 3  # Convert to percentage (0-100%)
                                if total_summaries > 0
                                else None  # Return None instead of 0 when no valid summaries
                            ),
                        },
                    },
                }
            )

            # Add overall analysis costs to totals if available
            if "overall_usage" in analysis and "overall_cost" in analysis:
                total_usage["input_tokens"] += analysis["overall_usage"].get(
                    "input_tokens", 0
                )
                total_usage["output_tokens"] += analysis["overall_usage"].get(
                    "output_tokens", 0
                )
                total_usage["total_tokens"] += analysis["overall_usage"].get(
                    "total_tokens", 0
                )
                total_cost["input_cost"] += analysis["overall_cost"].get(
                    "input_cost", 0.0
                )
                total_cost["output_cost"] += analysis["overall_cost"].get(
                    "output_cost", 0.0
                )
                total_cost["total_cost"] += analysis["overall_cost"].get(
                    "total_cost", 0.0
                )

            # Update with final totals
            analysis["total_usage"] = total_usage
            analysis["total_cost"] = total_cost

            # Add comprehensive timing information
            total_time = time.time() - analysis_start_time
            analysis["timing"] = {
                "total_processing_time_seconds": round(total_time, 3),
                "per_summary_times_seconds": [round(t, 3) for t in per_summary_timings],
                "average_per_summary_seconds": (
                    round(np.mean(per_summary_timings), 3) if per_summary_timings else 0
                ),
                "max_per_summary_seconds": (
                    round(max(per_summary_timings), 3) if per_summary_timings else 0
                ),
                "min_per_summary_seconds": (
                    round(min(per_summary_timings), 3) if per_summary_timings else 0
                ),
            }

            # Clean up intermediate files after successful completion
            if intermediate_dir and intermediate_dir.exists():
                try:
                    import shutil

                    shutil.rmtree(intermediate_dir)
                    self.log.info(
                        f"Cleaned up intermediate analysis files from: {intermediate_dir}"
                    )
                except Exception as e:
                    self.log.warning(
                        f"Failed to clean up intermediate directory {intermediate_dir}: {e}"
                    )

            return analysis

        except Exception as e:
            self.log.error(f"Error in summarization analysis: {e}")
            return {
                "overall_analysis": f"Summarization analysis failed: {str(e)}",
                "strengths": [],
                "weaknesses": ["Analysis failed to complete"],
                "recommendations": ["Check logs for error details"],
                "use_case_fit": "",
                "per_question": [],
                "overall_rating": {"rating": "error", "explanation": str(e)},
            }

    def generate_enhanced_report(
        self,
        results_path: str,
        output_dir: Optional[str] = None,
        groundtruth_path: Optional[str] = None,
        base_experiment_dir: Optional[str] = None,
    ) -> None:
        """
        Generate a detailed evaluation report including Claude's analysis.

        Args:
            results_path: Path to results JSON file
            output_dir: Optional dir path to save report. If None, returns the data.
            groundtruth_path: Optional path to groundtruth file for comparison (especially for summarization)
        """
        # Start timing
        report_start_time = time.time()

        try:
            if output_dir:
                output_path = Path(output_dir)
                output_path.mkdir(parents=True, exist_ok=True)

            # Get Claude analysis
            claude_analysis = self.analyze_with_claude(results_path, groundtruth_path)

            # Calculate total report generation time
            report_generation_time = time.time() - report_start_time

            # Create evaluation data without depending on threshold_metrics
            evaluation_data = {
                "metadata": {
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "model": self.claude.model,
                    "original_results_file": str(results_path),
                    "groundtruth_file": (
                        str(groundtruth_path) if groundtruth_path else None
                    ),
                    "report_generation_time_seconds": round(report_generation_time, 3),
                },
                **claude_analysis,
            }

            if output_dir:
                results_path_obj = Path(results_path)
                results_filename = results_path_obj.name

                # Preserve directory hierarchy if base_experiment_dir is provided
                if base_experiment_dir:
                    base_exp_path = Path(base_experiment_dir)
                    try:
                        # Calculate relative path from base experiment directory
                        relative_path = results_path_obj.relative_to(base_exp_path)
                        # Create the same directory structure in output
                        eval_subdir = output_path / relative_path.parent
                        eval_subdir.mkdir(parents=True, exist_ok=True)
                        json_path = eval_subdir / f"{results_path_obj.stem}.eval.json"
                    except ValueError:
                        # If results_path is not relative to base_experiment_dir, use flat structure
                        json_path = output_path / f"{results_path_obj.stem}.eval.json"
                else:
                    # Flat structure (original behavior)
                    json_path = output_path / f"{results_path_obj.stem}.eval.json"

                with open(json_path, "w") as f:
                    json.dump(evaluation_data, f, indent=2)
                self.log.info(f"Evaluation data saved to: {json_path}")

            return evaluation_data

        except Exception as e:
            self.log.error(f"Error during evaluation: {str(e)}")
            raise

    def create_template(
        self,
        groundtruth_file: str,
        output_dir: str = "./output/templates",
        similarity_threshold: float = 0.7,
    ) -> str:
        """
        Create a template results file from ground truth data for manual RAG evaluation.

        Args:
            groundtruth_file: Path to the ground truth JSON file
            output_dir: Directory to save the template file
            similarity_threshold: Similarity threshold for evaluation

        Returns:
            Path to the created template file
        """
        try:
            # Load ground truth data
            with open(groundtruth_file, "r", encoding="utf-8") as f:
                groundtruth_data = json.load(f)

            # Extract QA pairs from ground truth
            qa_pairs = groundtruth_data.get("analysis", {}).get("qa_pairs", [])
            if not qa_pairs:
                raise ValueError("No QA pairs found in ground truth file")

            # Create template structure
            template_data = {
                "metadata": {
                    "test_file": groundtruth_file,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "similarity_threshold": similarity_threshold,
                    "instructions": "Fill in the 'response' fields with your RAG system outputs, then evaluate using gaia eval",
                },
                "analysis": {"qa_results": []},
            }

            # Convert QA pairs to result template format
            for i, qa_pair in enumerate(qa_pairs):
                result_entry = {
                    "query": qa_pair.get("question", qa_pair.get("query", "")),
                    "ground_truth": qa_pair.get(
                        "answer",
                        qa_pair.get("response", qa_pair.get("ground_truth", "")),
                    ),
                    "response": f"[FILL IN YOUR RAG SYSTEM RESPONSE FOR QUESTION {i+1}]",
                }
                template_data["analysis"]["qa_results"].append(result_entry)

            # Create output directory
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)

            # Generate output filename
            groundtruth_filename = Path(groundtruth_file).stem
            if groundtruth_filename.endswith(".groundtruth"):
                base_name = groundtruth_filename[:-12]  # Remove '.groundtruth'
            else:
                base_name = groundtruth_filename

            template_filename = f"{base_name}.template.json"
            template_path = output_path / template_filename

            # Save template file
            with open(template_path, "w", encoding="utf-8") as f:
                json.dump(template_data, f, indent=2, ensure_ascii=False)

            self.log.info(f"Created template with {len(qa_pairs)} questions")
            return str(template_path)

        except Exception as e:
            self.log.error(f"Error creating template: {e}")
            raise

    def create_consolidated_evaluation_report(
        self, evaluation_files: List[str], output_dir: str, base_experiment_dir: str
    ) -> str:
        """Create a consolidated report of all evaluations."""
        from datetime import datetime

        output_base_path = Path(output_dir)

        # Load all evaluation results
        all_evaluations = []
        total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        total_cost = {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0}

        for eval_file in evaluation_files:
            # Find the actual evaluation file (could be in subdirectory)
            eval_paths = list(output_base_path.rglob(eval_file))
            if not eval_paths:
                self.log.warning(f"Evaluation file not found: {eval_file}")
                continue

            eval_path = eval_paths[0]  # Take first match

            try:
                with open(eval_path, "r", encoding="utf-8") as f:
                    evaluation_data = json.load(f)

                # For consolidated report, only include summary statistics
                eval_info = {
                    "experiment_name": eval_path.stem.replace(".eval", ""),
                    "file_path": str(eval_path.relative_to(output_base_path)),
                    "timestamp": evaluation_data.get("metadata", {}).get(
                        "timestamp", ""
                    ),
                    "model": evaluation_data.get("metadata", {}).get("model", ""),
                    "overall_rating": evaluation_data.get("overall_rating", {}),
                    "original_results_file": evaluation_data.get("metadata", {}).get(
                        "original_results_file", ""
                    ),
                    "usage": evaluation_data.get("total_usage", {}),
                    "cost": evaluation_data.get(
                        "total_cost", {}
                    ),  # This is evaluation cost
                }

                # Load the corresponding experiment file to get inference cost
                experiment_name = eval_path.stem.replace(".experiment.eval", "")

                # Preserve the subdirectory structure when looking for experiment file
                relative_eval_path = eval_path.relative_to(output_base_path)
                relative_dir = relative_eval_path.parent

                experiment_file = (
                    Path(base_experiment_dir)
                    / relative_dir
                    / f"{experiment_name}.experiment.json"
                )

                if experiment_file.exists():
                    try:
                        with open(experiment_file, "r", encoding="utf-8") as f:
                            experiment_data = json.load(f)
                        # Add inference cost from experiment file
                        eval_info["inference_cost"] = experiment_data.get(
                            "metadata", {}
                        ).get("total_cost", {})
                        eval_info["inference_usage"] = experiment_data.get(
                            "metadata", {}
                        ).get("total_usage", {})
                        eval_info["inference_type"] = experiment_data.get(
                            "metadata", {}
                        ).get("inference_type", "unknown")
                    except Exception as e:
                        self.log.warning(
                            f"Could not load experiment file {experiment_file}: {e}"
                        )
                        # Set default values for missing experiment data
                        eval_info["inference_cost"] = {
                            "input_cost": 0.0,
                            "output_cost": 0.0,
                            "total_cost": 0.0,
                        }
                        eval_info["inference_usage"] = {
                            "input_tokens": 0,
                            "output_tokens": 0,
                            "total_tokens": 0,
                        }
                        eval_info["inference_type"] = "unknown"
                else:
                    self.log.warning(f"Experiment file not found: {experiment_file}")
                    # Set default values for missing experiment data
                    eval_info["inference_cost"] = {
                        "input_cost": 0.0,
                        "output_cost": 0.0,
                        "total_cost": 0.0,
                    }
                    eval_info["inference_usage"] = {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                    }
                    eval_info["inference_type"] = "unknown"

                # Extract aspect summary if available (aggregate only)
                if evaluation_data.get("per_question"):
                    aspect_summary = {}
                    # Define the aspects we want to extract (matching visualization expectations)
                    # Map old aspect names to new ones for backwards compatibility
                    aspect_mapping = {
                        # Old names -> New names
                        "executive_summary_accuracy": "executive_summary_quality",
                        "completeness": "detail_completeness",
                        "action_items_accuracy": "action_items_structure",
                        "key_decisions_accuracy": "key_decisions_clarity",
                        "participant_identification": "participant_information",
                        "topic_coverage": "topic_organization",
                        # New names (map to themselves)
                        "executive_summary_quality": "executive_summary_quality",
                        "detail_completeness": "detail_completeness",
                        "action_items_structure": "action_items_structure",
                        "key_decisions_clarity": "key_decisions_clarity",
                        "participant_information": "participant_information",
                        "topic_organization": "topic_organization",
                    }

                    aspects = [
                        "executive_summary_quality",
                        "detail_completeness",
                        "action_items_structure",
                        "key_decisions_clarity",
                        "participant_information",
                        "topic_organization",
                    ]

                    for aspect in aspects:
                        aspect_ratings = []
                        for question in evaluation_data.get("per_question", []):
                            analysis = question.get("analysis", {})
                            # Check for the aspect using both old and new names
                            for old_name, new_name in aspect_mapping.items():
                                if new_name == aspect and old_name in analysis:
                                    rating = analysis[old_name].get("rating")
                                    if rating:
                                        aspect_ratings.append(rating)
                                    break

                        if aspect_ratings:
                            # Count occurrences of each rating
                            rating_counts = {}
                            for rating in aspect_ratings:
                                rating_counts[rating] = rating_counts.get(rating, 0) + 1

                            # Find most common rating
                            most_common = max(rating_counts.items(), key=lambda x: x[1])
                            aspect_summary[aspect] = {
                                "most_common_rating": most_common[0],
                                "rating_distribution": rating_counts,
                            }

                    if aspect_summary:
                        eval_info["aspect_summary"] = aspect_summary

                # Include timing summary if available
                if evaluation_data.get("timing"):
                    eval_info["avg_processing_time_seconds"] = evaluation_data[
                        "timing"
                    ].get(
                        "average_per_summary_seconds",
                        evaluation_data["timing"].get(
                            "total_processing_time_seconds", 0
                        ),
                    )

                all_evaluations.append(eval_info)

                # Accumulate totals
                usage = evaluation_data.get("total_usage", {})
                for key in total_usage:
                    total_usage[key] += usage.get(key, 0)

                cost = evaluation_data.get("total_cost", {})
                for key in total_cost:
                    total_cost[key] += cost.get(key, 0.0)

            except Exception as e:
                self.log.error(f"Error loading evaluation file {eval_path}: {e}")
                continue

        # Create consolidated report with enhanced metadata tracking
        evaluation_files_metadata = []
        for eval_file in evaluation_files:
            # Find the actual evaluation file (could be in subdirectory)
            eval_paths = list(output_base_path.rglob(eval_file))
            if eval_paths:
                eval_path = eval_paths[0]
                relative_path = str(eval_path.relative_to(output_base_path))
                evaluation_files_metadata.append(
                    {
                        "file_path": relative_path,
                        "added_at": datetime.now().isoformat(),
                        "last_modified": datetime.fromtimestamp(
                            eval_path.stat().st_mtime
                        ).isoformat(),
                        "fingerprint": self.get_evaluation_fingerprint(str(eval_path)),
                    }
                )

        consolidated_report = {
            "metadata": {
                "report_type": "consolidated_evaluations",
                "created_at": datetime.now().isoformat(),
                "last_updated": datetime.now().isoformat(),
                "timestamp": datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),  # Keep for backwards compatibility
                "experiment_directory": base_experiment_dir,
                "output_directory": output_dir,
                "total_evaluations": len(all_evaluations),
                "total_usage": total_usage,
                "total_cost": total_cost,
                "evaluation_files": evaluation_files_metadata,
            },
            "evaluations": all_evaluations,
        }

        # Save consolidated report
        consolidated_filename = "consolidated_evaluations_report.json"
        consolidated_path = output_base_path / consolidated_filename

        with open(consolidated_path, "w", encoding="utf-8") as f:
            json.dump(consolidated_report, f, indent=2)

        return str(consolidated_path)

    def get_evaluation_fingerprint(self, eval_file: str) -> str:
        """Generate fingerprint for evaluation file to detect changes.

        Args:
            eval_file: Path to the evaluation file

        Returns:
            Fingerprint string combining modification time and file size
        """
        eval_path = Path(eval_file)
        if not eval_path.exists():
            return ""

        # Use file modification time + file size as fingerprint
        stat = eval_path.stat()
        return f"{stat.st_mtime}_{stat.st_size}"

    def find_changed_evaluations(self, output_dir: str) -> List[str]:
        """Find evaluations that have changed since last consolidation.

        Args:
            output_dir: Output directory containing evaluations

        Returns:
            List of paths to changed evaluation files
        """
        output_base_path = Path(output_dir)
        consolidated_path = output_base_path / "consolidated_evaluations_report.json"

        if not consolidated_path.exists():
            return [str(f) for f in output_base_path.rglob("*.eval.json")]

        # Load existing fingerprints
        try:
            with open(consolidated_path, "r", encoding="utf-8") as f:
                existing_report = json.load(f)

            existing_fingerprints = {}
            if "evaluation_files" in existing_report.get("metadata", {}):
                for item in existing_report["metadata"]["evaluation_files"]:
                    existing_fingerprints[item["file_path"]] = item.get(
                        "fingerprint", ""
                    )
        except Exception as e:
            self.log.warning(f"Error reading existing consolidated report: {e}")
            return [str(f) for f in output_base_path.rglob("*.eval.json")]

        changed_files = []
        for eval_file in output_base_path.rglob("*.eval.json"):
            relative_path = str(eval_file.relative_to(output_base_path))
            current_fingerprint = self.get_evaluation_fingerprint(str(eval_file))

            if (
                relative_path not in existing_fingerprints
                or existing_fingerprints[relative_path] != current_fingerprint
            ):
                changed_files.append(str(eval_file))

        return changed_files

    def update_consolidated_evaluation_report(
        self,
        output_dir: str,
        new_eval_files: List[str] = None,
        regenerate: bool = False,
        base_experiment_dir: str = None,
    ) -> str:
        """Update consolidated report with new evaluations or regenerate completely.

        Args:
            output_dir: Output directory containing evaluations
            new_eval_files: List of new evaluation files to add (if None, auto-detect)
            regenerate: Force full regeneration of the report
            base_experiment_dir: Base experiment directory path

        Returns:
            Path to the consolidated report file
        """
        from datetime import datetime

        output_base_path = Path(output_dir)
        consolidated_filename = "consolidated_evaluations_report.json"
        consolidated_path = output_base_path / consolidated_filename

        if regenerate or not consolidated_path.exists():
            # Full regeneration (use existing logic)
            evaluation_files = [f.name for f in output_base_path.rglob("*.eval.json")]
            return self.create_consolidated_evaluation_report(
                evaluation_files, output_dir, base_experiment_dir or output_dir
            )

        # Load existing consolidated report
        try:
            with open(consolidated_path, "r", encoding="utf-8") as f:
                existing_report = json.load(f)
        except Exception as e:
            self.log.error(f"Error loading existing consolidated report: {e}")
            # Fallback to full regeneration
            evaluation_files = [f.name for f in output_base_path.rglob("*.eval.json")]
            return self.create_consolidated_evaluation_report(
                evaluation_files, output_dir, base_experiment_dir or output_dir
            )

        # Initialize metadata structure if missing
        if "evaluation_files" not in existing_report.get("metadata", {}):
            existing_report["metadata"]["evaluation_files"] = []

        # Find new evaluation files
        if not new_eval_files:
            all_eval_files = list(output_base_path.rglob("*.eval.json"))
            existing_files = {
                item["file_path"]
                for item in existing_report["metadata"]["evaluation_files"]
            }
            new_eval_files = [
                str(f)
                for f in all_eval_files
                if str(f.relative_to(output_base_path)) not in existing_files
            ]

        if not new_eval_files:
            self.log.info(
                "No new evaluations found - consolidated report is up to date"
            )
            return str(consolidated_path)

        self.log.info(
            f"Adding {len(new_eval_files)} new evaluations to consolidated report"
        )

        # Process new files and update report
        new_evaluations = []
        updated_usage = existing_report["metadata"].get(
            "total_usage", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        )
        updated_cost = existing_report["metadata"].get(
            "total_cost", {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0}
        )

        for eval_file in new_eval_files:
            eval_path = Path(eval_file)
            relative_path = str(eval_path.relative_to(output_base_path))

            # Add to metadata tracking
            existing_report["metadata"]["evaluation_files"].append(
                {
                    "file_path": relative_path,
                    "added_at": datetime.now().isoformat(),
                    "last_modified": datetime.fromtimestamp(
                        eval_path.stat().st_mtime
                    ).isoformat(),
                    "fingerprint": self.get_evaluation_fingerprint(str(eval_path)),
                }
            )

            # Load and integrate evaluation data
            try:
                with open(eval_path, "r", encoding="utf-8") as f:
                    eval_data = json.load(f)

                # Create evaluation summary (similar to existing logic)
                eval_info = {
                    "experiment_name": eval_path.stem.replace(".eval", ""),
                    "file_path": relative_path,
                    "timestamp": eval_data.get("metadata", {}).get("timestamp", ""),
                    "model": eval_data.get("metadata", {}).get("model", "unknown"),
                }

                # Add overall analysis if available
                if "overall_analysis" in eval_data:
                    eval_info["overall_analysis"] = (
                        eval_data["overall_analysis"][:200] + "..."
                        if len(eval_data["overall_analysis"]) > 200
                        else eval_data["overall_analysis"]
                    )

                # Add timing info if available
                if eval_data.get("timing"):
                    eval_info["avg_processing_time_seconds"] = eval_data["timing"].get(
                        "average_per_summary_seconds",
                        eval_data["timing"].get("total_processing_time_seconds", 0),
                    )

                new_evaluations.append(eval_info)

                # Accumulate usage and cost
                usage = eval_data.get("total_usage", {})
                for key in updated_usage:
                    updated_usage[key] += usage.get(key, 0)

                cost = eval_data.get("total_cost", {})
                for key in updated_cost:
                    updated_cost[key] += cost.get(key, 0.0)

            except Exception as e:
                self.log.error(f"Error processing new evaluation file {eval_path}: {e}")
                continue

        # Update the consolidated report
        existing_report["evaluations"].extend(new_evaluations)
        existing_report["metadata"]["last_updated"] = datetime.now().isoformat()
        existing_report["metadata"]["total_evaluations"] = len(
            existing_report["evaluations"]
        )
        existing_report["metadata"]["total_usage"] = updated_usage
        existing_report["metadata"]["total_cost"] = updated_cost

        # Save updated report
        with open(consolidated_path, "w", encoding="utf-8") as f:
            json.dump(existing_report, f, indent=2)

        self.log.info(
            f"Updated consolidated report with {len(new_evaluations)} new evaluations"
        )
        return str(consolidated_path)

    def _detect_evaluation_type(self, models_data: List[Dict]) -> str:
        """Detect whether this is a RAG or summarization evaluation based on the data structure."""
        if not models_data:
            return "unknown"

        # Check first model's per_question data structure
        first_model = models_data[0]
        per_question = first_model.get("per_question", [])

        if not per_question:
            return "unknown"

        # Look at the first question to determine evaluation type
        first_question = per_question[0]

        # Summarization evaluations have specific analysis fields
        analysis = first_question.get("analysis", {})
        # Check for new aspect names
        if any(
            key in analysis
            for key in [
                "executive_summary_quality",
                "detail_completeness",
                "action_items_structure",
                "key_decisions_clarity",
                "participant_information",
                "topic_organization",
            ]
        ):
            return "summarization"

        # Also check for old aspect names (for backwards compatibility)
        if any(
            key in analysis
            for key in [
                "executive_summary_accuracy",
                "completeness",
                "action_items_accuracy",
                "key_decisions_accuracy",
                "participant_identification",
                "topic_coverage",
            ]
        ):
            return "summarization"

        # RAG/QA evaluations have similarity scores and different structure
        # Both Q&A and RAG evaluations are treated the same way
        if "similarity_score" in first_question or "passed_threshold" in first_question:
            return "rag"

        # Additional check for Q&A evaluations that have qa_inputs
        if "qa_inputs" in first_question:
            return "rag"

        # If we can't detect the evaluation type, log the issue for debugging
        self.log.warning(
            f"Could not detect evaluation type from data structure: {list(first_question.keys())}"
        )
        return "unknown"

    def _generate_summarization_report(self, models_data: List[Dict]) -> str:
        """Generate markdown content specifically for summarization evaluation reports."""

        # Build performance ranking based on overall quality ratings
        ranking = []
        for model in models_data:
            # Count quality ratings from per_question data
            excellent_count = 0
            good_count = 0
            fair_count = 0
            poor_count = 0

            for question in model.get("per_question", []):
                analysis = question.get("analysis", {})
                overall_quality = analysis.get("overall_quality", "")
                if overall_quality == "excellent":
                    excellent_count += 1
                elif overall_quality == "good":
                    good_count += 1
                elif overall_quality == "fair":
                    fair_count += 1
                elif overall_quality == "poor":
                    poor_count += 1
                # Note: "error" and other invalid ratings are excluded from ranking

            total_summaries = excellent_count + good_count + fair_count + poor_count
            if total_summaries > 0:
                quality_score_raw = (
                    excellent_count * 4
                    + good_count * 3
                    + fair_count * 2
                    + poor_count * 1
                ) / total_summaries
                quality_score_percentage = ((quality_score_raw - 1) / 3) * 100
                ranking.append(f"**{model['name']}** ({quality_score_percentage:.1f}%)")

        ranking_text = " > ".join(ranking)

        # Determine production readiness for summarization
        production_ready = any(
            "excellent" in str(m.get("per_question", [])) for m in models_data
        )
        production_note = (
            "Some models show excellent summarization capabilities."
            if production_ready
            else "All models need improvement for production summarization."
        )

        # Build metrics table for summarization
        table_rows = []
        for model in models_data:
            # Count quality ratings
            excellent_count = 0
            good_count = 0
            fair_count = 0
            poor_count = 0

            for question in model.get("per_question", []):
                analysis = question.get("analysis", {})
                overall_quality = analysis.get("overall_quality", "")
                if overall_quality == "excellent":
                    excellent_count += 1
                elif overall_quality == "good":
                    good_count += 1
                elif overall_quality == "fair":
                    fair_count += 1
                elif overall_quality == "poor":
                    poor_count += 1
                # Note: "error" and other invalid ratings are excluded from metrics

            total_summaries = excellent_count + good_count + fair_count + poor_count
            excellent_rate = (
                (excellent_count / total_summaries * 100) if total_summaries > 0 else 0
            )

            rating_map = {
                "excellent": "Excellent",
                "good": "Good",
                "fair": "Fair",
                "poor": "Poor",
                "unknown": "Unknown",
            }
            rating = rating_map.get(model["rating"], model["rating"].title())

            table_rows.append(
                f"| **{model['name']}** | {excellent_rate:.0f}% | {excellent_count}/{total_summaries} | {good_count} | {fair_count} | {poor_count} | {rating} |"
            )

        # Identify common summarization issues
        failure_patterns = []

        # Analyze common weaknesses across models
        all_weaknesses = []
        for model in models_data:
            all_weaknesses.extend(model.get("weaknesses", []))

        if "Manual review recommended" in str(all_weaknesses):
            failure_patterns.append("**Quality Consistency Issues** (Multiple Models)")
            failure_patterns.append("- Manual review recommended for complex summaries")
            failure_patterns.append(
                "- Inconsistent quality across different summary types"
            )
            failure_patterns.append("- Need for human validation of critical details")

        # Check for specific summarization challenges
        poor_performers = [
            m for m in models_data if "poor" in str(m.get("per_question", []))
        ]
        if poor_performers:
            failure_patterns.append("")
            failure_patterns.append(
                "**Content Structure Issues** "
                + f"({', '.join([m['name'] for m in poor_performers])})"
            )
            failure_patterns.append("- Poor action item organization and clarity")
            failure_patterns.append("- Missing key decisions or incomplete details")
            failure_patterns.append("- Inadequate participant information capture")

        # Model-specific analysis for summarization
        model_analyses = []

        if models_data:
            best = models_data[0]
            best_strengths = (
                best["strengths"][:2]
                if best["strengths"]
                else ["Maintains summary structure", "Comprehensive analysis performed"]
            )
            best_weakness = (
                best["weaknesses"][0]
                if best["weaknesses"]
                else "Needs validation for complex scenarios"
            )

            model_analyses.append(f"### **{best['name']}** - Best Performer")
            model_analyses.append(f"- **Strengths**: {', '.join(best_strengths)}")
            model_analyses.append(f"- **Weakness**: {best_weakness}")
            model_analyses.append(
                f"- **Actionable**: Implement quality validation workflows, standardize summary templates"
            )

            if len(models_data) > 1:
                worst = models_data[-1]
                worst_issues = (
                    worst["weaknesses"][:2]
                    if worst["weaknesses"]
                    else ["Inconsistent summary quality"]
                )

                model_analyses.append("")
                model_analyses.append(f"### **{worst['name']}** - Needs Improvement")
                model_analyses.append(f"- **Issues**: {', '.join(worst_issues)}")
                model_analyses.append(
                    f"- **Actionable**: Enhance prompt engineering, add structured output validation"
                )

        # Cost efficiency analysis
        cost_analyses = []
        if all(m["total_cost"] > 0 for m in models_data):
            for model in models_data:
                roi_desc = (
                    "best value"
                    if model == models_data[0]
                    else (
                        "poor value"
                        if "poor" in str(model.get("per_question", []))
                        else "moderate value"
                    )
                )
                cost_analyses.append(
                    f"- **{model['name']}**: ${model['total_cost']:.3f} total cost, {roi_desc} for summarization quality"
                )

        # Technical actions for summarization
        tech_actions = [
            "1. **Summary Template Standardization**: Create consistent output formats for different meeting types",
            "2. **Quality Validation Pipeline**: Implement automated checks for completeness and accuracy",
            "3. **Prompt Engineering Optimization**: Improve prompts for better action item extraction and decision clarity",
        ]

        tech_actions.extend(
            [
                "4. **Human-in-the-Loop Validation**: Add review workflows for critical summaries",
                "5. **Meeting Type Classification**: Tailor summarization approach based on meeting context",
                "6. **Output Formatting Enhancement**: Improve structure and readability of generated summaries",
            ]
        )

        # Investment decision for summarization
        if models_data:
            best_model = models_data[0]
            if "excellent" in str(best_model.get("per_question", [])):
                investment_decision = f"**{best_model['name']}** shows production potential with proper validation workflows."
                timeline = "2-4 weeks for validation pipeline implementation."
            else:
                investment_decision = (
                    "All models require improvement before reliable production use."
                )
                timeline = "4-8 weeks for prompt optimization and quality improvements."
        else:
            investment_decision = (
                "Unable to recommend specific model - insufficient evaluation data."
            )
            timeline = "Timeline uncertain due to limited baseline data."

        # Build the complete summarization report
        report = f"""# Meeting Summarization Performance Analysis: {len(models_data)} LLM Comparison

## Executive Summary
Performance ranking: {ranking_text}

{production_note}

## Key Performance Metrics

| Model | Excellent Rate | Excellent/Total | Good | Fair | Poor | Rating |
|-------|----------------|-----------------|------|------|------|---------|
{chr(10).join(table_rows)}

## Common Challenges

{chr(10).join(failure_patterns)}

## Model-Specific Analysis

{chr(10).join(model_analyses)}

## Cost Efficiency Analysis
{chr(10).join(cost_analyses) if cost_analyses else "Cost data not available for analysis"}

## Immediate Improvement Actions

### High Priority (Quality Enhancement)
{chr(10).join(tech_actions[:3])}

### Medium Priority (Process Optimization)
{chr(10).join(tech_actions[3:])}

## Bottom Line
**Investment decision**: {investment_decision} **Timeline**: {timeline}"""

        return report

    def generate_summary_report(
        self, eval_dir: str, output_path: str = "LLM_RAG_Evaluation_Report.md"
    ) -> Dict:
        """
        Generate a comprehensive summary report from multiple evaluation files.

        Args:
            eval_dir: Directory containing .eval.json files
            output_path: Path to save the markdown report

        Returns:
            Dict containing summary data
        """
        try:
            eval_path = Path(eval_dir)
            if not eval_path.exists():
                raise FileNotFoundError(f"Evaluation directory not found: {eval_dir}")

            # Find all .eval.json files (recursively)
            eval_files = list(eval_path.rglob("*.eval.json"))
            if not eval_files:
                raise FileNotFoundError(f"No .eval.json files found in {eval_dir}")

            self.log.info(f"Found {len(eval_files)} evaluation files")

            # Parse evaluation data
            models_data = []
            for eval_file in eval_files:
                try:
                    with open(eval_file, "r", encoding="utf-8") as f:
                        eval_data = json.load(f)

                    # Extract model name from filename or metadata
                    filename = eval_file.stem
                    model_name = filename.replace(".eval", "")

                    # Extract key metrics
                    overall_rating = eval_data.get("overall_rating", {})
                    metrics = overall_rating.get("metrics", {})
                    total_cost = eval_data.get("total_cost", {})

                    # Calculate quality score for summarization evaluations
                    quality_score = 0.0
                    overall_rating_metrics = overall_rating.get("metrics", {})
                    if overall_rating_metrics:
                        # Use existing quality_score if available (could be None for error cases)
                        quality_score = overall_rating_metrics.get("quality_score", 0.0)
                        if quality_score is None:
                            quality_score = 0.0  # Treat None as 0 for ranking purposes
                    else:
                        # Calculate from per_question data if metrics not available
                        excellent_count = 0
                        good_count = 0
                        fair_count = 0
                        poor_count = 0

                        for question in eval_data.get("per_question", []):
                            analysis = question.get("analysis", {})
                            overall_quality = analysis.get("overall_quality", "")
                            if overall_quality == "excellent":
                                excellent_count += 1
                            elif overall_quality == "good":
                                good_count += 1
                            elif overall_quality == "fair":
                                fair_count += 1
                            elif overall_quality == "poor":
                                poor_count += 1
                            # Note: "error" and other invalid ratings are excluded from quality score calculation

                        total_summaries = (
                            excellent_count + good_count + fair_count + poor_count
                        )
                        if total_summaries > 0:
                            quality_score_raw = (
                                excellent_count * 4
                                + good_count * 3
                                + fair_count * 2
                                + poor_count * 1
                            ) / total_summaries
                            quality_score = ((quality_score_raw - 1) / 3) * 100

                    model_info = {
                        "name": model_name,
                        "filename": eval_file.name,
                        "pass_rate": metrics.get("pass_rate", 0),
                        "accuracy": metrics.get("accuracy_percentage", 0),
                        "mean_similarity": metrics.get("mean_similarity", 0),
                        "std_similarity": metrics.get("std_similarity", 0),
                        "min_similarity": metrics.get("min_similarity", 0),
                        "max_similarity": metrics.get("max_similarity", 0),
                        "num_questions": metrics.get("num_questions", 0),
                        "num_passed": metrics.get("num_passed", 0),
                        "num_failed": metrics.get("num_failed", 0),
                        "threshold": metrics.get("similarity_threshold", 0.7),
                        "rating": overall_rating.get("rating", "unknown"),
                        "quality_score": quality_score,  # Add quality score to model info
                        "total_cost": total_cost.get("total_cost", 0),
                        "analysis": eval_data.get("overall_analysis", ""),
                        "strengths": eval_data.get("strengths", []),
                        "weaknesses": eval_data.get("weaknesses", []),
                        "recommendations": eval_data.get("recommendations", []),
                        "per_question": eval_data.get("per_question", []),
                    }
                    models_data.append(model_info)

                except Exception as e:
                    self.log.warning(f"Error processing {eval_file}: {e}")
                    continue

            if not models_data:
                raise ValueError("No valid evaluation data found")

            # Detect evaluation type first
            evaluation_type = self._detect_evaluation_type(models_data)

            # Sort by appropriate metric based on evaluation type
            if evaluation_type == "summarization":
                # Sort by quality score (descending) for summarization
                models_data.sort(key=lambda x: x["quality_score"], reverse=True)
            else:
                # Sort by pass rate (descending) for RAG and unknown types
                models_data.sort(key=lambda x: x["pass_rate"], reverse=True)

            if evaluation_type == "summarization":
                report_content = self._generate_summarization_report(models_data)
            elif evaluation_type == "rag":
                report_content = self._generate_markdown_report(models_data)
            else:
                # Handle unknown evaluation type
                self.log.error(
                    f"Unknown evaluation type detected: {evaluation_type}. Cannot generate report."
                )
                raise ValueError(
                    f"Unsupported evaluation type: {evaluation_type}. Expected 'summarization' or 'rag'."
                )

            # Save report
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(report_content)

            self.log.info(f"Summary report saved to: {output_path}")

            return {
                "models_analyzed": len(models_data),
                "report_path": output_path,
                "summary_data": models_data,
                "evaluation_type": evaluation_type,
            }

        except Exception as e:
            self.log.error(f"Error generating summary report: {e}")
            raise

    def _generate_markdown_report(self, models_data: List[Dict]) -> str:
        """Generate markdown content for the summary report."""

        # Create executive summary
        best_model = models_data[0] if models_data else None
        worst_model = models_data[-1] if models_data else None

        # Build performance ranking
        ranking = []
        for i, model in enumerate(models_data):
            ranking.append(f"**{model['name']}** ({model['pass_rate']:.0%})")
        ranking_text = " > ".join(ranking)

        # Determine if any model meets production standards
        production_ready = any(
            m["pass_rate"] >= 0.7 and m["mean_similarity"] >= 0.7 for m in models_data
        )
        production_note = (
            "None achieve production standards (70% pass rate + 0.7 similarity)."
            if not production_ready
            else "Some models approach production readiness."
        )

        # Build metrics table
        table_rows = []
        for model in models_data:
            rating_map = {
                "excellent": "Excellent",
                "good": "Good",
                "fair": "Fair",
                "poor": "Poor",
                "unknown": "Unknown",
            }
            rating = rating_map.get(model["rating"], model["rating"].title())
            table_rows.append(
                f"| **{model['name']}** | {model['pass_rate']:.0%} | {model['mean_similarity']:.3f} | {model['std_similarity']:.3f} | {rating} |"
            )

        # Identify failure patterns
        failure_patterns = []

        # Knowledge retrieval gaps (check if models consistently fail on specific question types)
        knowledge_issues = [m for m in models_data if m["mean_similarity"] < 0.4]
        if len(knowledge_issues) >= 2:
            failure_patterns.append("**Knowledge Retrieval Gaps** (All Models)")
            failure_patterns.append("- Unable to access specific document sections")
            failure_patterns.append("- Missing organizational information")
            failure_patterns.append(
                "- Poor semantic matching between queries and knowledge base"
            )

        # Factual accuracy issues
        accuracy_issues = [m for m in models_data if m["pass_rate"] < 0.5]
        if accuracy_issues:
            failure_patterns.append("")
            failure_patterns.append(
                "**Factual Accuracy Issues** "
                + f"({', '.join([m['name'] for m in accuracy_issues])})"
            )
            # Add specific issues from analysis
            for model in accuracy_issues[:3]:  # Limit to top 3 worst performers
                if (
                    "jurisdictional" in model["analysis"].lower()
                    or "confusion" in model["analysis"].lower()
                ):
                    failure_patterns.append(
                        f"- **{model['name']}**: Jurisdictional confusion (US vs Canadian regulations)"
                    )
                if (
                    "incorrect" in model["analysis"].lower()
                    or "wrong" in model["analysis"].lower()
                ):
                    failure_patterns.append(
                        f"- **{model['name']}**: Incorrect core values, wrong regulatory stages"
                    )

        # Completeness problems
        if len([m for m in models_data if m["mean_similarity"] < 0.5]) >= 2:
            failure_patterns.append("")
            failure_patterns.append("**Completeness Problems** (All Models)")
            failure_patterns.append("- Partial answers missing key regulatory details")
            failure_patterns.append(
                "- Incomplete permit types (missing multiple authorization categories)"
            )
            failure_patterns.append("- Poor handling of comprehensive queries")

        # Model-specific analysis
        model_analyses = []

        if models_data:
            best = models_data[0]
            best_strengths = (
                best["strengths"][:2]
                if best["strengths"]
                else ["Good performance when information is available"]
            )
            best_weakness = (
                best["weaknesses"][0]
                if best["weaknesses"]
                else "Inconsistent retrieval quality"
            )

            model_analyses.append(f"### **{best['name']}** - Best Performer")
            model_analyses.append(f"- **Strengths**: {', '.join(best_strengths)}")
            model_analyses.append(f"- **Weakness**: {best_weakness}")
            model_analyses.append(
                f"- **Actionable**: Improve retrieval consistency, expand knowledge base coverage"
            )

            if len(models_data) > 1:
                worst = models_data[-1]
                worst_issues = (
                    worst["weaknesses"][:2]
                    if worst["weaknesses"]
                    else ["Poor overall performance"]
                )

                model_analyses.append("")
                model_analyses.append(f"### **{worst['name']}** - Needs Improvement")
                model_analyses.append(f"- **Issues**: {', '.join(worst_issues)}")
                model_analyses.append(
                    f"- **Actionable**: Requires significant system improvements before production use"
                )

        # Cost efficiency analysis
        cost_analyses = []
        if all(m["total_cost"] > 0 for m in models_data):
            for model in models_data:
                roi_desc = (
                    "best ROI"
                    if model == models_data[0]
                    else ("poor ROI" if model["pass_rate"] < 0.3 else "moderate ROI")
                )
                cost_analyses.append(
                    f"- **{model['name']}**: ${model['total_cost']:.3f} total cost, {roi_desc} at {model['pass_rate']:.0%} accuracy"
                )

        # Technical actions
        tech_actions = [
            "1. **Document Indexing Overhaul**: Fix content gaps, improve chunking strategy",
            "2. **Embedding Model Upgrade**: Current semantic matching insufficient (mean similarity <0.4)",
            "3. **Context Validation**: Implement regulatory framework filters",
        ]

        if any("runtime" in str(m["weaknesses"]).lower() for m in models_data):
            tech_actions.append(
                "4. **Token Limit Fixes**: Address runtime errors and token constraints"
            )

        tech_actions.extend(
            [
                "5. **Response Validation**: Add factual accuracy checks before output",
                "6. **Retrieval Redundancy**: Multi-step retrieval for complex queries",
            ]
        )

        # Investment decision
        if best_model:
            if best_model["pass_rate"] >= 0.5:
                investment_decision = f"Focus resources on **{best_model['name']}** optimization rather than fixing underperforming models."
            else:
                investment_decision = "All models require significant improvement before production deployment."

            timeline = "3-6 months minimum before regulatory compliance readiness."
        else:
            investment_decision = (
                "Unable to recommend specific model - all require substantial work."
            )
            timeline = "Timeline uncertain due to poor baseline performance."

        # Build the complete report
        report = f"""# RAG System Performance Analysis: {len(models_data)} LLM Comparison

## Executive Summary
Performance ranking: {ranking_text}

{production_note}

## Key Performance Metrics

| Model | Pass Rate | Mean Similarity | Std Dev | Rating |
|-------|-----------|----------------|---------|---------|
{chr(10).join(table_rows)}

## Critical Failure Patterns

{chr(10).join(failure_patterns)}

## Model-Specific Analysis

{chr(10).join(model_analyses)}

## Cost Efficiency Analysis
{chr(10).join(cost_analyses) if cost_analyses else "Cost data not available for analysis"}

## Immediate Technical Actions

### High Priority (Critical Fixes)
{chr(10).join(tech_actions[:3])}

### Medium Priority (Performance Optimization)
{chr(10).join(tech_actions[3:])}

## Bottom Line
**Investment decision**: {investment_decision} **Timeline**: {timeline}"""

        return report


if __name__ == "__main__":
    # Example usage
    evaluator = Evaluator()
    results_file = "./output/rag/introduction.results.json"

    try:
        evaluation_data = evaluator.generate_enhanced_report(
            results_file, output_dir="./output/eval"
        )

        # Print key metrics from the analysis
        overall_rating = evaluation_data.get("overall_rating", {})
        print("\nStatus:", overall_rating.get("rating", "N/A"))
        print("Explanation:", overall_rating.get("explanation", ""))

        # Print metrics if available
        metrics = overall_rating.get("metrics", {})
        if metrics:
            print("\nMetrics:")
            print(f"Number of questions: {metrics.get('num_questions', 'N/A')}")
            print(
                f"Similarity threshold: {metrics.get('similarity_threshold', 'N/A'):.3f}"
            )
            print(f"Pass rate: {metrics.get('pass_rate', 'N/A'):.3f}")
            print(f"Passed threshold: {metrics.get('num_passed', 'N/A')}")
            print(f"Failed threshold: {metrics.get('num_failed', 'N/A')}")
            print("\nSimilarity Statistics:")
            print(f"Mean: {metrics.get('mean_similarity', 'N/A'):.3f}")
            print(f"Median: {metrics.get('median_similarity', 'N/A'):.3f}")
            print(f"Min: {metrics.get('min_similarity', 'N/A'):.3f}")
            print(f"Max: {metrics.get('max_similarity', 'N/A'):.3f}")
            print(f"Standard deviation: {metrics.get('std_similarity', 'N/A'):.3f}")

        print("\nAnalysis:", evaluation_data.get("overall_analysis", "N/A"))

        # Print cost information if available
        if evaluation_data.get("total_usage") and evaluation_data.get("total_cost"):
            total_usage = evaluation_data["total_usage"]
            total_cost = evaluation_data["total_cost"]
            print("\nCost Analysis:")
            print(
                f"Token usage: {total_usage['input_tokens']:,} input + {total_usage['output_tokens']:,} output = {total_usage['total_tokens']:,} total"
            )
            print(
                f"Total cost: ${total_cost['input_cost']:.4f} input + ${total_cost['output_cost']:.4f} output = ${total_cost['total_cost']:.4f} total"
            )
            if evaluation_data.get("per_question"):
                print(
                    f"Average cost per question: ${total_cost['total_cost']/len(evaluation_data['per_question']):.4f}"
                )

        if evaluation_data.get("strengths"):
            print("\nStrengths:")
            for strength in evaluation_data["strengths"]:
                print(f"- {strength}")

    except Exception as e:
        print(f"Error during evaluation: {e}")
