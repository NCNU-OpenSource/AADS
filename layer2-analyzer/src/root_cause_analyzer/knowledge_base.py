"""
Knowledge Base - Vector database for historical cases

Uses ChromaDB + sentence-transformers for semantic search.
"""
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path
import chromadb
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)


class KnowledgeBase:
    """
    Knowledge base for historical anomaly cases

    Features:
    - Semantic search using embeddings
    - Store diagnosis and resolution pairs
    - Learn from confirmed anomalies
    - RAG (Retrieval-Augmented Generation) for LLM
    """

    def __init__(
        self,
        db_path: str = "/app/data/chromadb",
        embedding_model: str = "all-MiniLM-L6-v2",
        collection_name: str = "anomaly_cases"
    ):
        """
        Initialize knowledge base

        Args:
            db_path: Path to ChromaDB storage
            embedding_model: Sentence transformer model name
            collection_name: ChromaDB collection name
        """
        self.db_path = Path(db_path)
        self.db_path.mkdir(parents=True, exist_ok=True)

        self.embedding_model_name = embedding_model
        self.collection_name = collection_name

        # Initialize ChromaDB with new API
        self.client = chromadb.PersistentClient(path=str(self.db_path))

        # Get or create collection
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"description": "Historical anomaly diagnosis cases"}
        )

        # Load embedding model
        logger.info(f"Loading embedding model: {embedding_model}")
        self.embedder = SentenceTransformer(embedding_model)

        self.stats = {
            "total_cases": self.collection.count(),
            "total_searches": 0,
            "total_additions": 0
        }

        logger.info(f"Knowledge base initialized with {self.stats['total_cases']} cases")

    def add_case(
        self,
        case_id: str,
        anomaly_pattern: str,
        diagnosis_summary: str,
        root_cause: str,
        resolution: str,
        effectiveness: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """
        Add a case to knowledge base

        Args:
            case_id: Unique case identifier
            anomaly_pattern: Description of anomaly pattern
            diagnosis_summary: Diagnosis summary
            root_cause: Root cause analysis
            resolution: Resolution steps
            effectiveness: Effectiveness score (0-1)
            metadata: Additional metadata
        """
        # Create document (concatenate all text for embedding)
        document = f"{anomaly_pattern}\n{diagnosis_summary}\n{root_cause}\n{resolution}"

        # Generate embedding
        embedding = self.embedder.encode(document).tolist()

        # Prepare metadata
        case_metadata = {
            "diagnosis_summary": diagnosis_summary,
            "root_cause": root_cause,
            "resolution": resolution,
            "effectiveness": effectiveness
        }
        if metadata:
            case_metadata.update(metadata)

        # Add to collection
        self.collection.add(
            ids=[case_id],
            embeddings=[embedding],
            documents=[anomaly_pattern],
            metadatas=[case_metadata]
        )

        self.stats["total_additions"] += 1
        self.stats["total_cases"] += 1

        logger.info(f"Added case to knowledge base: {case_id}")

    def search_similar_cases(
        self,
        query: str,
        n_results: int = 5,
        min_effectiveness: float = 0.5
    ) -> List[Dict[str, Any]]:
        """
        Search for similar cases

        Args:
            query: Query text (anomaly description)
            n_results: Number of results to return
            min_effectiveness: Minimum effectiveness score filter

        Returns:
            List of similar case dictionaries
        """
        # Generate query embedding
        query_embedding = self.embedder.encode(query).tolist()

        # Search collection
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results
        )

        self.stats["total_searches"] += 1

        # Parse results
        cases = []
        if results['ids']:
            for i, case_id in enumerate(results['ids'][0]):
                metadata = results['metadatas'][0][i]
                effectiveness = metadata.get('effectiveness', 0.0)

                # Filter by effectiveness
                if effectiveness >= min_effectiveness:
                    cases.append({
                        'case_id': case_id,
                        'anomaly_pattern': results['documents'][0][i],
                        'diagnosis_summary': metadata.get('diagnosis_summary', ''),
                        'root_cause': metadata.get('root_cause', ''),
                        'resolution': metadata.get('resolution', ''),
                        'effectiveness': effectiveness,
                        'distance': results['distances'][0][i] if 'distances' in results else None
                    })

        logger.info(f"Found {len(cases)} similar cases for query (total candidates: {len(results['ids'][0])})")

        return cases

    def update_case_effectiveness(self, case_id: str, effectiveness: float):
        """
        Update effectiveness score for a case

        Args:
            case_id: Case identifier
            effectiveness: New effectiveness score (0-1)
        """
        # Get current case
        result = self.collection.get(ids=[case_id])
        if not result['ids']:
            logger.warning(f"Case not found: {case_id}")
            return

        # Update metadata
        metadata = result['metadatas'][0]
        metadata['effectiveness'] = effectiveness

        # Update collection
        self.collection.update(
            ids=[case_id],
            metadatas=[metadata]
        )

        logger.info(f"Updated effectiveness for case {case_id}: {effectiveness}")

    def delete_case(self, case_id: str):
        """
        Delete a case from knowledge base

        Args:
            case_id: Case identifier
        """
        self.collection.delete(ids=[case_id])
        self.stats["total_cases"] -= 1
        logger.info(f"Deleted case: {case_id}")

    def get_stats(self) -> Dict[str, Any]:
        """Get knowledge base statistics"""
        self.stats["total_cases"] = self.collection.count()
        return dict(self.stats)

    def persist(self):
        """Persist changes to disk"""
        self.client.persist()
        logger.info("Knowledge base persisted to disk")
