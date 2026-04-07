"""
LogBERT Anomaly Detection Engine
Uses pre-trained BERT model for log anomaly detection
"""
import torch
from transformers import BertTokenizer, BertForMaskedLM
from typing import List, Dict, Any
import numpy as np


class AnomalyDetector:
    def __init__(
        self,
        model_path: str = "bert-base-uncased",
        threshold: float = 0.5,
        device: str = None
    ):
        """
        Initialize LogBERT anomaly detector

        Args:
            model_path: Path to fine-tuned model or HuggingFace model name
            threshold: Anomaly score threshold (higher = more anomalous)
            device: 'cuda' or 'cpu' (auto-detect if None)
        """
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.threshold = threshold

        # Load BERT tokenizer and model
        self.tokenizer = BertTokenizer.from_pretrained(model_path)
        self.model = BertForMaskedLM.from_pretrained(model_path)
        self.model.to(self.device)
        self.model.eval()

    def compute_anomaly_score(self, log_sequence: List[str]) -> float:
        """
        Compute anomaly score using masked prediction loss

        Higher score = more anomalous (deviates from normal patterns)
        """
        # Join log templates into sequence
        text = " [SEP] ".join(log_sequence)

        # Tokenize
        inputs = self.tokenizer(
            text,
            return_tensors="pt",
            max_length=512,
            truncation=True,
            padding=True
        ).to(self.device)

        # Create masked version (mask 15% of tokens)
        labels = inputs["input_ids"].clone()
        mask_prob = 0.15
        # Ensure mask_indices is on the same device as labels
        mask_indices = (torch.rand(labels.shape, device=self.device) < mask_prob)
        mask_indices = mask_indices & (labels != self.tokenizer.pad_token_id)
        mask_indices = mask_indices & (labels != self.tokenizer.cls_token_id)
        mask_indices = mask_indices & (labels != self.tokenizer.sep_token_id)

        inputs["input_ids"][mask_indices] = self.tokenizer.mask_token_id

        # Compute loss (prediction error)
        with torch.no_grad():
            outputs = self.model(**inputs, labels=labels)
            loss = outputs.loss.item()

        return loss

    def detect_anomalies(
        self,
        logs: List[Dict[str, Any]],
        window_size: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Detect anomalies in log sequences

        Args:
            logs: Processed logs with templates
            window_size: Number of logs per sequence

        Returns:
            Logs with anomaly scores and labels
        """
        results = []

        # Process logs in sliding windows
        for i in range(0, len(logs), window_size):
            window = logs[i:i + window_size]
            if len(window) < 3:  # Skip too-small windows
                continue

            templates = [log["template"] for log in window]
            score = self.compute_anomaly_score(templates)
            is_anomaly = score > self.threshold

            for log in window:
                results.append({
                    **log,
                    "anomaly_score": score,
                    "is_anomaly": is_anomaly
                })

        return results
