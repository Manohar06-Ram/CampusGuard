"""
CampusGuard — Spatio-Temporal Kinematic Action Analyzer
Analyzes velocity and acceleration of joints over time to reduce false positives.
"""

import numpy as np
from collections import defaultdict, deque
import logging

logger = logging.getLogger("CampusGuard.TemporalAnalyzer")

class TemporalActionAnalyzer:
    def __init__(self, window_size=15):
        # Store a sliding window of keypoints for each tracked person
        # Format: { track_id: deque([frame1_keypoints, frame2_keypoints, ...]) }
        self.pose_history = defaultdict(lambda: deque(maxlen=window_size))
        
        # Kinematic thresholds (tuned for human motion)
        self.PUNCH_VELOCITY_THRESHOLD = 0.15  # Fast movement
        self.PUNCH_ACCELERATION_THRESHOLD = 0.05 # Sudden spike in speed
        self.AGGRESSIVE_WINDOW = 8 # Number of frames to sustain the motion

    def analyze(self, track_id: int, current_keypoints: list) -> dict:
        """
        Analyze the movement of a person based on their MediaPipe keypoints.
        current_keypoints: List of 33 (x, y, z, visibility) tuples from MediaPipe.
        Returns: {'action': str, 'confidence': float}
        """
        if not current_keypoints or len(current_keypoints) < 33:
            return {'action': 'IDLE', 'confidence': 0.0}

        # Add current frame to history
        self.pose_history[track_id].append(current_keypoints)
        history = self.pose_history[track_id]

        # We need at least 3 frames to calculate acceleration (velocity needs 2, accel needs 3)
        if len(history) < 3:
            return {'action': 'CALIBRATING', 'confidence': 0.0}

        # Focus on upper body: Left/Right Wrists (15, 16) and Shoulders (11, 12)
        # We calculate movement relative to the shoulders to ignore body swaying
        left_wrist_idx, right_wrist_idx = 15, 16
        left_shoulder_idx, right_shoulder_idx = 11, 12

        # Get the last 3 frames
        p0 = history[-3] # Oldest
        p1 = history[-2] # Middle
        p2 = history[-1] # Newest (Current)

        # Calculate velocity (change in position) and acceleration (change in velocity)
        # We check both hands
        is_violent = False
        max_confidence = 0.0

        for wrist_idx, shoulder_idx in [(left_wrist_idx, left_shoulder_idx), (right_wrist_idx, right_shoulder_idx)]:
            # Check visibility
            if p2[wrist_idx][3] < 0.5 or p2[shoulder_idx][3] < 0.5:
                continue

            # 1. Calculate Velocity (Distance between frame 1 and 2)
            v1 = self._calculate_relative_distance(p1[wrist_idx], p1[shoulder_idx], p2[wrist_idx], p2[shoulder_idx])
            
            # 2. Calculate Previous Velocity (Distance between frame 0 and 1)
            v0 = self._calculate_relative_distance(p0[wrist_idx], p0[shoulder_idx], p1[wrist_idx], p1[shoulder_idx])

            # 3. Calculate Acceleration (Change in velocity)
            acceleration = abs(v1 - v0)

            # Spatio-Temporal Logic:
            # A punch has HIGH velocity AND HIGH acceleration (sudden spike)
            if v1 > self.PUNCH_VELOCITY_THRESHOLD and acceleration > self.PUNCH_ACCELERATION_THRESHOLD:
                # Check if the wrist is moving forward (Z-depth) or aggressively across X/Y
                is_violent = True
                # Confidence is based on how extreme the acceleration is
                confidence = min(1.0, (v1 * acceleration) * 10) 
                max_confidence = max(max_confidence, confidence)

        if is_violent:
            return {'action': 'AGGRESSIVE_STRIKE', 'confidence': round(max_confidence, 2)}
        
        # Check for "Flailing" (High velocity but low acceleration = waving/dancing)
        if v1 > self.PUNCH_VELOCITY_THRESHOLD and acceleration < self.PUNCH_ACCELERATION_THRESHOLD:
            return {'action': 'FAST_MOVEMENT', 'confidence': 0.5}

        return {'action': 'IDLE', 'confidence': 0.0}

    def _calculate_relative_distance(self, p1_wrist, p1_shoulder, p2_wrist, p2_shoulder):
        """Calculate how far the wrist moved relative to the shoulder (ignoring body translation)"""
        # Normalize wrist position relative to shoulder in frame 1
        r1_x = p1_wrist[0] - p1_shoulder[0]
        r1_y = p1_wrist[1] - p1_shoulder[1]
        
        # Normalize wrist position relative to shoulder in frame 2
        r2_x = p2_wrist[0] - p2_shoulder[0]
        r2_y = p2_wrist[1] - p2_shoulder[1]

        # Euclidean distance of the change
        dist = np.sqrt((r2_x - r1_x)**2 + (r2_y - r1_y)**2)
        return dist

    def reset_track(self, track_id: int):
        """Clear history if a person leaves the frame"""
        if track_id in self.pose_history:
            del self.pose_history[track_id]