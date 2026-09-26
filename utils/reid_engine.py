"""
CampusGuard — Person Re-Identification Engine
Extracts visual embeddings to track people across cameras/time
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision import models
import cv2
import numpy as np
from collections import defaultdict
import logging

logger = logging.getLogger("CampusGuard.ReID")

class PersonReID:
    def __init__(self, device='cuda'):
        self.device = device
        self.embedding_dim = 512
        
        # Load a lightweight ReID model
        self.model = self._build_reid_model()
        self.model.to(device)
        self.model.eval()
        
        # Database: person_id -> list of embeddings
        self.person_database = defaultdict(list)
        self.next_global_id = 1
        
        # Similarity threshold (0.0 to 1.0)
        self.similarity_threshold = 0.60
        
        logger.info(f"✅ ReID Engine initialized on {device.upper()}")
    
    def _build_reid_model(self):
        """Build a lightweight ReID feature extractor"""
        # Use ResNet18 for speed (updated to use modern 'weights' parameter)
        backbone = models.resnet18(weights=models.ResNet18_Weights.DEFAULT)
        
        # Remove the final classification layer (PARENTHESES ADDED HERE)
        modules = list(backbone.children())[:-1]
        model = nn.Sequential(*modules)
        
        return model
    
    def extract_embedding(self, person_crop):
        """
        Extract a 512-dim embedding from a person's image crop
        person_crop: numpy array (H, W, 3) in BGR format
        Returns: normalized embedding vector
        """
        if person_crop is None or person_crop.size == 0:
            return None
        
        # Preprocess: resize to 64x128 (standard lightweight ReID input)
        person_crop = cv2.resize(person_crop, (64, 128))
        person_crop = cv2.cvtColor(person_crop, cv2.COLOR_BGR2RGB)
        person_crop = person_crop.astype(np.float32) / 255.0
        
        # Normalize (ImageNet stats)
        mean = np.array([0.485, 0.456, 0.406])
        std = np.array([0.229, 0.224, 0.225])
        person_crop = (person_crop - mean) / std
        
        # Convert to tensor: (H, W, C) -> (C, H, W) -> (1, C, H, W)
        tensor = torch.from_numpy(person_crop).permute(2, 0, 1).unsqueeze(0)
        # Explicitly force float32 to prevent DoubleTensor mismatch
        tensor = tensor.to(torch.float32).to(self.device)
        
        # Extract features
        with torch.no_grad():
            embedding = self.model(tensor)
            embedding = embedding.view(embedding.size(0), -1)
            # L2 normalize
            embedding = F.normalize(embedding, p=2, dim=1)
        
        return embedding.cpu().numpy().flatten()
    
    def compute_similarity(self, emb1, emb2):
        """
        Compute cosine similarity between two embeddings
        Returns: similarity score (0.0 to 1.0)
        """
        if emb1 is None or emb2 is None:
            return 0.0
        
        # Cosine similarity
        similarity = np.dot(emb1, emb2) / (np.linalg.norm(emb1) * np.linalg.norm(emb2) + 1e-8)
        return float(similarity)
    
    def identify_person(self, embedding):
        """
        Search database for matching person
        Returns: (global_id, is_new_person)
        """
        if embedding is None:
            return self.next_global_id, True
        
        best_similarity = 0.0
        best_id = None
        
        # Search all known persons
        for person_id, embeddings in self.person_database.items():
            # Compare against all stored embeddings for this person
            for stored_emb in embeddings:
                sim = self.compute_similarity(embedding, stored_emb)
                if sim > best_similarity:
                    best_similarity = sim
                    best_id = person_id
        
        # If similarity is high enough, it's a known person
        if best_similarity >= self.similarity_threshold and best_id is not None:
            # Add this new embedding to the person's database
            self.person_database[best_id].append(embedding)
            # Keep only last 10 embeddings per person (memory management)
            if len(self.person_database[best_id]) > 60:
                self.person_database[best_id] = self.person_database[best_id][-10:]
            return best_id, False
        else:
            # New person
            new_id = self.next_global_id
            self.next_global_id += 1
            self.person_database[new_id].append(embedding)
            return new_id, True
    
    def get_database_stats(self):
        """Return statistics about the ReID database"""
        return {
            'total_persons': len(self.person_database),
            'next_id': self.next_global_id
        }