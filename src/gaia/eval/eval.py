from pathlib import Path
import json
import numpy as np
from typing import Dict, List, Optional
from gaia.logger import get_logger
from gaia.eval.claude import ClaudeClient
from datetime import datetime


class RagEvaluator:
    """Evaluates RAG system performance using test results."""

    def __init__(self, model="claude-3-7-sonnet-20250219"):
        self.log = get_logger(__name__)
        self.claude = ClaudeClient(model=model)

    def load_results(self, results_path: str) -> Dict:
        """Load test results from a JSON file."""
        try:
            with open(results_path, "r") as f:
                return json.load(f)
        except Exception as e:
            self.log.error(f"Error loading results file: {e}")
            raise

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

        # Calculate metrics
        similarities = [result["similarity"] for result in qa_results]

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
                "num_passed": sum(
                    1
                    for s in similarities
                    if s >= results["metadata"]["similarity_threshold"]
                ),
                "num_failed": sum(
                    1
                    for s in similarities
                    if s < results["metadata"]["similarity_threshold"]
                ),
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

    def analyze_with_claude(self, results_path: str) -> Dict:
        """
        Use Claude to perform qualitative analysis of RAG results.

        Args:
            results_path: Path to results JSON file

        Returns:
            Dict containing Claude's analysis
        """
        try:
            results = self.load_results(results_path)
            qa_results = results.get("analysis", {}).get(
                "qa_results", results.get("qa_results", [])
            )

            # Initialize analysis structure
            analysis = {
                "overall_analysis": "",
                "strengths": [],
                "weaknesses": [],
                "recommendations": [],
                "use_case_fit": "",
                "per_question": [],
                "overall_rating": {"rating": "", "explanation": ""},
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
                for qa_result in qa_results:
                    # Restructure the qa_result into qa_inputs
                    qa_inputs = {
                        "query": qa_result["query"],
                        "ground_truth": qa_result["ground_truth"],
                        "response": qa_result["response"],
                        "similarity": qa_result["similarity"],
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

                    response = self.claude.get_completion(prompt)

                    try:
                        # Extract JSON and combine with qa_inputs
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
                            # Add qa_inputs as a nested dictionary
                            qa_analysis["qa_inputs"] = qa_inputs
                            analysis["per_question"].append(qa_analysis)
                        else:
                            self.log.error(f"No JSON found in response for question")
                            analysis["per_question"].append(
                                {
                                    "error": "Failed to parse analysis",
                                    "raw_response": response_text,
                                    "qa_inputs": qa_inputs,
                                }
                            )
                    except Exception as e:
                        self.log.error(f"Error processing analysis: {e}")
                        analysis["per_question"].append(
                            {
                                "error": str(e),
                                "raw_response": str(response),
                                "qa_inputs": qa_inputs,
                            }
                        )

                # After analyzing all questions, get overall analysis
                overall_prompt = f"""
                Review these RAG system test results and provide an overall analysis.

                Number of questions: {len(qa_results)}
                Similarity threshold: {results["metadata"]["similarity_threshold"]}
                Number passed threshold: {sum(1 for r in qa_results if r['similarity'] >= results["metadata"]["similarity_threshold"])}
                Number failed threshold: {sum(1 for r in qa_results if r['similarity'] < results["metadata"]["similarity_threshold"])}
                Pass rate: {sum(1 for r in qa_results if r['similarity'] >= results["metadata"]["similarity_threshold"]) / len(qa_results):.3f}

                Similarity statistics:
                - Mean: {np.mean([r['similarity'] for r in qa_results]):.3f}
                - Median: {np.median([r['similarity'] for r in qa_results]):.3f}
                - Min: {np.min([r['similarity'] for r in qa_results]):.3f}
                - Max: {np.max([r['similarity'] for r in qa_results]):.3f}
                - Standard Deviation: {np.std([r['similarity'] for r in qa_results]):.3f}

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

                overall_response = self.claude.get_completion(overall_prompt)

                try:
                    # Extract JSON from overall response
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
                        analysis.update(overall_analysis)
                    else:
                        self.log.error("No JSON found in overall analysis response")
                        analysis.update(
                            {
                                "error": "Failed to parse overall analysis",
                                "raw_response": response_text,
                            }
                        )
                except Exception as e:
                    self.log.error(f"Error processing overall analysis: {e}")
                    analysis.update(
                        {"error": str(e), "raw_response": str(overall_response)}
                    )

                return analysis
            except Exception as api_error:
                if "529" in str(api_error) or "overloaded" in str(api_error).lower():
                    self.log.warning(
                        "Claude API is currently overloaded. Returning partial analysis with raw data."
                    )
                    # Include raw QA results without Claude analysis
                    for qa_result in qa_results:
                        qa_inputs = {
                            "query": qa_result["query"],
                            "ground_truth": qa_result["ground_truth"],
                            "response": qa_result["response"],
                            "similarity": qa_result["similarity"],
                        }
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

    def generate_enhanced_report(
        self, results_path: str, output_dir: Optional[str] = None
    ) -> None:
        """
        Generate a detailed evaluation report including Claude's analysis.

        Args:
            results_path: Path to results JSON file
            output_dir: Optional dir path to save report. If None, returns the data.
        """
        try:
            if output_dir:
                output_path = Path(output_dir)
                output_path.mkdir(parents=True, exist_ok=True)

            # Get Claude analysis
            claude_analysis = self.analyze_with_claude(results_path)

            # Create evaluation data without depending on threshold_metrics
            evaluation_data = {
                "metadata": {
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "model": self.claude.model,
                    "original_results_file": str(results_path),
                },
                **claude_analysis,
            }

            if output_dir:
                results_filename = Path(results_path).name
                json_path = output_path / f"{Path(results_filename).stem}.eval.json"
                with open(json_path, "w") as f:
                    json.dump(evaluation_data, f, indent=2)
                self.log.info(f"Evaluation data saved to: {json_path}")

            return evaluation_data

        except Exception as e:
            self.log.error(f"Error during evaluation: {str(e)}")
            raise


if __name__ == "__main__":
    # Example usage
    evaluator = RagEvaluator()
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

        if evaluation_data.get("strengths"):
            print("\nStrengths:")
            for strength in evaluation_data["strengths"]:
                print(f"- {strength}")

    except Exception as e:
        print(f"Error during evaluation: {e}")
