import unittest
from pathlib import Path
import tempfile
import os
import shutil
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import json
from datetime import datetime
import asyncio

from gaia.logger import get_logger
from gaia.agents.Rag.app import MyAgent
from gaia.cli import GaiaCliClient


class TestRagAgent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Set up test environment once before all tests."""
        cls.log = get_logger(__name__)

        # Create a temporary directory for test files
        cls.temp_dir = Path(tempfile.mkdtemp())

        # Define test documents
        cls.test_doc = "./data/html/blender/introduction.html"
        cls.groundtruth_json = "./output/groundtruth/introduction.groundtruth.json"
        cls.output_dir = "./output/rag"

        # Initialize GaiaCliClient to start servers
        cls.client = GaiaCliClient(
            agent_name="Rag",
            model="llama3.2:1b",
            input_file=cls.test_doc,
            logging_level="INFO",
        )
        cls.client.start()

        # Initialize TF-IDF vectorizer
        cls.vectorizer = TfidfVectorizer(stop_words="english")

        # Load QA pairs from JSON if available
        cls.qa_pairs = {}
        try:
            with open(cls.groundtruth_json, "r") as f:
                test_data = json.load(f)
                cls.qa_pairs = {
                    qa_pair["query"]: qa_pair["response"]
                    for qa_pair in test_data["analysis"]["qa_pairs"]
                }
        except FileNotFoundError:
            cls.log.warning(f"Groundtruth file not found: {cls.groundtruth_json}")
            cls.log.warning("QA comparison tests will be skipped")

    @classmethod
    def tearDownClass(cls):
        """Clean up resources after all tests are done."""
        # Stop the GaiaCliClient servers
        cls.client.stop()

        # Remove temporary directory and all its contents
        shutil.rmtree(cls.temp_dir)

    def calculate_similarity(self, text1, text2):
        """Calculate cosine similarity between two texts"""
        tfidf_matrix = self.vectorizer.fit_transform([text1, text2])
        return cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:2])[0][0]

    async def async_test_qa_responses(self):
        """Test RAG responses against ground truth."""
        if not self.qa_pairs:
            self.skipTest(
                "No groundtruth data available - skipping QA comparison tests"
            )

        print("Starting test_qa_responses")  # Debug print

        # Wait for server to be ready
        self.client.wait_for_servers()

        SIMILARITY_THRESHOLD = 0.7  # Adjust this threshold as needed

        # Load original test data to preserve metadata and summary
        with open(self.groundtruth_json, "r") as f:
            test_data = json.load(f)

        # Create results in same format as input, but with responses
        results = {
            "metadata": {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "test_file": self.test_doc,
                "similarity_threshold": SIMILARITY_THRESHOLD,
            },
            "analysis": {
                "source": test_data["analysis"]["source"],
                "summary": test_data["analysis"]["summary"],
                "qa_results": [],
            },
        }

        # Run tests and collect results
        for qa_pair in test_data["analysis"]["qa_pairs"]:
            query = qa_pair["query"]
            ground_truth = qa_pair["response"]

            # Use client's prompt method to send query
            response = ""
            async for chunk in self.client.prompt(query):
                response += chunk

            # Parse the JSON response to get just the text
            try:
                response_json = json.loads(response)
                response_text = response_json.get("response", "")
            except json.JSONDecodeError:
                response_text = response  # Fallback to raw response if not JSON

            similarity = self.calculate_similarity(ground_truth, response_text)

            result = {
                "query": query,
                "ground_truth": ground_truth,
                "response": response_text,
                "similarity": float(similarity),
            }

            results["analysis"]["qa_results"].append(result)

        # Save results to JSON file
        os.makedirs(self.output_dir, exist_ok=True)
        results_file = Path(
            self.output_dir,
            Path(self.groundtruth_json).name.split(".")[0] + ".results.json",
        )
        with open(results_file, "w") as f:
            json.dump(results, f, indent=2)
        self.log.info(f"\nResults saved to: {results_file.absolute()}")

        # Print detailed results
        self.log.info("\nDetailed Test Results:")
        self.log.info("-" * 80)
        for result in results["analysis"]["qa_results"]:
            self.log.info(f"Query: {result['query']}")
            self.log.info(f"Similarity Score: {result['similarity']:.2f}")
            self.log.info(f"Ground Truth: {result['ground_truth']}")
            self.log.info(f"Response: {result['response']}")
            self.log.info("-" * 80)

            # Assert similarity threshold after logging
            self.assertGreaterEqual(
                result["similarity"],
                SIMILARITY_THRESHOLD,
                f"\nQuery: {result['query']}\nExpected: {result['ground_truth']}\nGot: {result['response']}\nSimilarity: {result['similarity']:.2f}",
            )

    def test_qa_responses(self):
        """Wrapper for async test_qa_responses"""
        asyncio.run(self.async_test_qa_responses())

    def test_build_index(self):
        """Test building an index from a file."""
        index = self.client.build_index(self.test_doc)
        self.assertIsNotNone(index)
        self.assertIsNotNone(self.client.index)

    def test_save_and_load_index(self):
        """Test saving and loading an index."""
        # Build and save index
        index_path = self.temp_dir / "index"
        self.log.info(f"Building index at {index_path}")
        self.client.build_index(self.test_doc, output_path=index_path)

        loaded_index = self.client.load_index(index_path)
        self.assertIsNotNone(loaded_index)

        # Get index stats
        index_stats = loaded_index.ref_doc_info
        self.log.info(f"Index contains {len(index_stats)} documents")

        # Check vector store
        vector_store = loaded_index.vector_store
        self.assertIsNotNone(vector_store)
        self.log.info(f"Vector store type: {type(vector_store).__name__}")

    async def async_test_query_engine(self):
        """Test query engine setup and basic query."""
        # Wait for server to be ready
        self.client.wait_for_servers()

        # Test query
        query = "What is the top bar used for?"
        response = ""
        async for chunk in self.client.prompt(query):
            response += chunk
        self.assertIsNotNone(response)
        self.log.info(f"Query response: {response}")

    def test_query_engine(self):
        """Wrapper for async test_query_engine"""
        asyncio.run(self.async_test_query_engine())


if __name__ == "__main__":
    # Create and run test suite with all tests
    suite = unittest.TestLoader().loadTestsFromTestCase(TestRagAgent)
    # unittest.TextTestRunner().run(suite)

    # Commented out individual test runs for reference
    specific_tests = unittest.TestSuite()
    specific_tests.addTest(TestRagAgent("test_qa_responses"))
    # specific_tests.addTest(TestRagAgent("test_build_index"))
    # specific_tests.addTest(TestRagAgent("test_save_and_load_index"))
    # specific_tests.addTest(TestRagAgent("test_query_engine"))
    unittest.TextTestRunner().run(specific_tests)
