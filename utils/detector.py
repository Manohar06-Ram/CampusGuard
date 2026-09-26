"""
CampusGuard — Security Detector & AI Engine v3 (High-Accuracy Mode)
Features: YOLO Small/Medium Model, Object Tracking, Temporal Consensus, ReID.
"""

import cv2
import numpy as np
import threading
import time
import os
import math
import logging
from datetime import datetime
from collections import defaultdict, deque
from typing import Optional, Dict, List, Tuple, Any
from utils.temporal_analyzer import TemporalActionAnalyzer

# ─ AI & Hardware Imports ─────────────────────────────────────────────────────
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False
    logging.warning("⚠️ Ultralytics (YOLO) not installed. Object detection disabled.")

try:
    import mediapipe as mp
    if hasattr(mp, 'solutions') and hasattr(mp.solutions, 'pose'):
        MP_POSE = mp.solutions.pose
        MEDIAPIPE_AVAILABLE = True
    else:
        MEDIAPIPE_AVAILABLE = False
        MP_POSE = None
except ImportError:
    MEDIAPIPE_AVAILABLE = False
    MP_POSE = None
    logging.warning("⚠️ MediaPipe not installed. Pose detection disabled.")

# ─ ReID Import ────────────────────────────────────────────────────────────────
try:
    from utils.reid_engine import PersonReID
    REID_AVAILABLE = True
except ImportError:
    REID_AVAILABLE = False
    logging.warning("⚠️ ReID Engine not found. Cross-camera tracking disabled.")

try:
    import torch
    GPU_AVAILABLE = torch.cuda.is_available()
    GPU_NAME = torch.cuda.get_device_name(0) if GPU_AVAILABLE else None
    GPU_VRAM = round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 1) if GPU_AVAILABLE else 0
except Exception:
    GPU_AVAILABLE = False
    GPU_NAME = None
    GPU_VRAM = 0

CURRENT_DEVICE = "cuda" if GPU_AVAILABLE else "cpu"

# Configure professional logging
logger = logging.getLogger("CampusGuard.Detector")
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)-8s | %(message)s', datefmt='%H:%M:%S')

# ── Constants & Configurations ────────────────────────────────────────────────

WEAPON_CLASSES: Dict[str, Tuple[str, str, str]] = {
    'knife':        ('SHARP_OBJECT', 'CRITICAL', '🔪 Knife / Sharp object detected'),
    'scissors':     ('SHARP_OBJECT', 'CRITICAL', '✂️  Scissors detected'),
    'baseball bat': ('BLUNT_WEAPON', 'HIGH',     '🏏 Blunt weapon detected'),
    'bottle':       ('BLUNT_WEAPON', 'HIGH',     '🍾 Bottle — potential weapon'),
    'fork':         ('SHARP_OBJECT', 'HIGH',     '🍴 Sharp object detected'),
    'wine glass':   ('SHARP_OBJECT', 'MEDIUM',   '🥂 Breakable object detected'),
    'cell phone':   ('SURVEILLANCE', 'MEDIUM',   '📱 Unauthorized recording device'),
}

# MediaPipe Pose Landmark Indices
NOSE, LEFT_WRIST, RIGHT_WRIST = 0, 15, 16
LEFT_ELBOW, RIGHT_ELBOW = 13, 14
LEFT_SHOULDER, RIGHT_SHOULDER = 11, 12
LEFT_HIP, RIGHT_HIP = 23, 24
LEFT_KNEE, RIGHT_KNEE = 25, 26


# ── Night Vision Engine ───────────────────────────────────────────────────────

class NightVisionEngine:
    def __init__(self):
        self.clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        self.mode = "NORMAL"
        self.current_brightness = 255
        self.auto_active = False

    def set_mode(self, mode: str) -> None:
        self.mode = mode.upper()

    def get_mode(self) -> str:
        return self.mode

    def process(self, frame: np.ndarray) -> np.ndarray:
        if frame is None:
            return frame
        
        ycrcb = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
        self.current_brightness = int(np.mean(ycrcb[:, :, 0]))
        
        if self.mode == "NORMAL":
            return frame
        elif self.mode == "NIGHT":
            return self._night(frame)
        elif self.mode == "THERMAL":
            return self._thermal(frame)
        elif self.mode == "AUTO":
            if self.current_brightness < 80:
                self.auto_active = True
                return self._night(frame)
            else:
                self.auto_active = False
                return frame
        return frame

    def _night(self, frame: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        enh = self.clahe.apply(gray)
        den = cv2.fastNlMeansDenoising(enh, h=10)
        k = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        sharp = cv2.filter2D(den, -1, k)
        out = np.zeros_like(frame)
        out[:, :, 0] = (sharp * 0.15).astype(np.uint8)
        out[:, :, 1] = sharp
        out[:, :, 2] = (sharp * 0.10).astype(np.uint8)
        return out

    def _thermal(self, frame: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        enh = self.clahe.apply(gray)
        den = cv2.fastNlMeansDenoising(enh, h=8)
        inv = cv2.bitwise_not(den)
        thm = cv2.applyColorMap(inv, cv2.COLORMAP_JET)
        return cv2.addWeighted(thm, 0.85, frame, 0.15, 0)

    def preprocess_for_detection(self, frame: np.ndarray) -> np.ndarray:
        ycrcb = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
        if int(np.mean(ycrcb[:, :, 0])) > 80 and self.mode == "NORMAL":
            return frame.astype(np.uint8)
        
        y, cr, cb = cv2.split(ycrcb)
        ye = self.clahe.apply(y)
        yd = cv2.fastNlMeansDenoising(ye, h=8)
        k = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        ys = cv2.filter2D(yd, -1, k)
        enh = cv2.merge([ys, cr, cb])
        return cv2.cvtColor(enh, cv2.COLOR_YCrCb2BGR).astype(np.uint8)
        
    def draw_overlay(self, frame: np.ndarray) -> np.ndarray:
        if frame is None:
            return frame
        h, w = frame.shape[:2]
        labels = {
            "NORMAL":  ("NORMAL",    (180, 180, 180)),
            "NIGHT":   ("NIGHT VIS", (50, 220, 50)),
            "THERMAL": ("THERMAL",   (60, 100, 220)),
            "AUTO":    ("AUTO-NV" if self.auto_active else "AUTO-DAY",
                        (50, 220, 50) if self.auto_active else (180, 180, 180))
        }
        label, colour = labels.get(self.mode, ("NORMAL", (180, 180, 180)))
        cv2.putText(frame, f"◉ {label}", (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1, cv2.LINE_AA)
        
        bx, by = w - 90, h - 60
        cv2.rectangle(frame, (bx, by), (bx + 80, by + 6), (40, 40, 40), -1)
        fill = int(80 * self.current_brightness / 255)
        bc = (50, 220, 50) if self.current_brightness > 80 else (50, 200, 220)
        cv2.rectangle(frame, (bx, by), (bx + fill, by + 6), bc, -1)
        cv2.putText(frame, f"LUX {self.current_brightness}", (bx, by - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (160, 160, 160), 1)
        return frame


# ── Main Security Detector ────────────────────────────────────────────────────

class SecurityDetector:
    def __init__(self, source=0, camera_id="CAM-01"):
        self.source = source
        self.camera_id = camera_id
        self.cap: Optional[cv2.VideoCapture] = None
        self.running = False
        self.lock = threading.Lock()
        self.current_frame: Optional[np.ndarray] = None
        self.detection_results: List[Dict[str, Any]] = []
        
        # Performance metrics
        self.fps = 0.0
        self.frame_count = 0
        self.start_time = time.time()
        
        # AI Models
        self.model: Optional[YOLO] = None
        self.pose_detector: Optional[Any] = None
        self.device = CURRENT_DEVICE
        self.nv = NightVisionEngine()
        
        # --- UPGRADE #1: Initialize ReID Engine ---
        try:
            if REID_AVAILABLE:
                self.reid_engine = PersonReID(device=self.device)
                logger.info("✅ ReID Engine initialized")
            else:
                self.reid_engine = None
        except Exception as e:
            logger.warning(f"⚠️ ReID Engine failed to initialize: {e}")
            self.reid_engine = None
        
        # --- UPGRADE #2: Initialize Spatio-Temporal Analyzer ---
        self.temporal_analyzer = TemporalActionAnalyzer(window_size=15)
        logger.info("✅ Spatio-Temporal Action Analyzer initialized")
        
        # Tracking & History
        self.person_tracker: Dict[str, float] = {}
        self.last_alert_time: Dict[str, float] = {}
        self.motion_history = deque(maxlen=15)
        self.prev_frame_gray: Optional[np.ndarray] = None
        self.pose_history: Dict[str, deque] = {}
        self.wrist_counter: Dict[str, int] = {}
        self.fight_threshold = 0.08
        
        # 🎯 HIGH ACCURACY: Temporal Consensus Tracking
        self.weapon_track_history: Dict[int, int] = defaultdict(int)
        self.alerted_tracks: set = set()
        self.CONSENSUS_FRAMES = 2  # Optimized for faster detection

        # Services (Injected later to avoid circular imports)
        self.logger_service = None
        self.alerter_service = None

        # Security Zones
        self.zones: Dict[str, Dict[str, Any]] = {
            "Main Entrance":    {"coords": [0.0, 0.0, 0.5, 0.5], "loiter_threshold": 300, "restricted_after": "18:00", "color": [0, 255, 0]},
            "Restricted Lab":   {"coords": [0.5, 0.0, 1.0, 0.5], "loiter_threshold": 15,  "restricted_after": "00:00", "color": [0, 0, 255]},
            "Parking Lot":      {"coords": [0.0, 0.5, 0.5, 1.0], "loiter_threshold": 180, "restricted_after": "20:00", "color": [255, 165, 0]},
            "Principal Office": {"coords": [0.5, 0.5, 1.0, 1.0], "loiter_threshold": 20,  "restricted_after": "09:00", "color": [128, 0, 128]},
        }
        
        os.makedirs('static/screenshots', exist_ok=True)
        self._load_models()

    def set_services(self, logger_service: Any, alerter_service: Any) -> None:
        self.logger_service = logger_service
        self.alerter_service = alerter_service

    def _load_models(self) -> None:
        if YOLO_AVAILABLE:
            try:
                # Prioritize Small model for maximum FPS on laptop GPUs, fallback to Medium
                for name in ['yolo26s.pt', 'yolo26m.pt', 'yolo11s.pt']:
                    try:
                        self.model = YOLO(name)
                        self.model.to(self.device)
                        logger.info(f"✅ YOLO loaded: {name} on {self.device.upper()} (High-Accuracy Mode)")
                        break
                    except Exception as e:
                        logger.error(f"⚠️ FAILED to load {name}. Error: {e}")
                        continue
            except Exception as e:
                logger.error(f"⚠️ YOLO initialization error: {e}")
                
        if MEDIAPIPE_AVAILABLE:
            try:
                self.pose_detector = MP_POSE.Pose(
                    static_image_mode=False, model_complexity=1,
                    smooth_landmarks=True,
                    min_detection_confidence=0.5,
                    min_tracking_confidence=0.5
                )
                logger.info("✅ MediaPipe Pose detector ready")
            except Exception as e:
                logger.error(f"⚠️ MediaPipe initialization error: {e}")

    def start(self) -> bool:
        if self.running:
            return True
            
        self.cap = cv2.VideoCapture(self.source)
        if not self.cap.isOpened():
            logger.warning(f"⚠️ No camera detected for {self.camera_id}. Starting Demo Mode.")
            self.cap = None
            self.running = True
            threading.Thread(target=self._demo_loop, daemon=True, name="DemoThread").start()
            return True
            
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        
        self.running = True
        threading.Thread(target=self._detection_loop, daemon=True, name="DetectionThread").start()
        return True

    def stop(self) -> None:
        self.running = False
        if self.cap:
            self.cap.release()
            self.cap = None
        logger.info(f"🛑 Camera {self.camera_id} and detection stopped.")

    def _detection_loop(self) -> None:
        reconnect_attempts = 0
        max_reconnect_attempts = 5
        
        while self.running:
            if not self.cap or not self.cap.isOpened():
                reconnect_attempts += 1
                if reconnect_attempts <= max_reconnect_attempts:
                    logger.warning(f"🔄 Attempting camera reconnect ({reconnect_attempts}/{max_reconnect_attempts})...")
                    self.cap = cv2.VideoCapture(self.source)
                    time.sleep(2)
                else:
                    logger.error("📷 Camera disconnected permanently. Switching to Demo Mode.")
                    self.cap = None
                    self._demo_loop()
                    break
                continue
            
            ret, frame = self.cap.read()
            if not ret:
                time.sleep(0.033)
                continue
                
            reconnect_attempts = 0
            self.frame_count += 1
            
            elapsed = time.time() - self.start_time
            if elapsed > 1.0:
                self.fps = self.frame_count / elapsed
                self.frame_count = 0
                self.start_time = time.time()
                
            h, w = frame.shape[:2]
            detections: List[Dict[str, Any]] = []
            alerts_this_frame: List[Dict[str, Any]] = []
            person_boxes: List[List[int]] = []
            person_track_ids: List[int] = []
            
            # 1. YOLO Object Detection + ByteTrack
            detection_frame = self.nv.preprocess_for_detection(frame)
            if self.model:
                try:
                    results = self.model.track(
                        detection_frame, 
                        persist=True, 
                        verbose=False, 
                        conf=0.25, 
                        iou=0.45, 
                        tracker="bytetrack.yaml", 
                        device=0,
                        imgsz=640  # Optimized for laptop GPU stability
                    )
                    
                    current_track_ids = set()
                    
                    for result in results:
                        if result.boxes is None:
                            continue
                        
                        track_ids = result.boxes.id.int().tolist() if result.boxes.id is not None else [None] * len(result.boxes)
                        
                        for box, track_id in zip(result.boxes, track_ids):
                            cls_id = int(box.cls[0])
                            cls_name = result.names[cls_id].lower()
                            conf = float(box.conf[0])
                            x1, y1, x2, y2 = map(int, box.xyxy[0])
                            
                            detections.append({
                                'class': cls_name, 'confidence': round(conf, 2), 
                                'bbox': [x1, y1, x2, y2], 'track_id': track_id
                            })
                            
                            if cls_name == 'person':
                                person_boxes.append([x1, y1, x2, y2])
                                person_track_ids.append(track_id)
                                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2

                                # --- UPGRADE #1: REID Processing (DISABLED FOR SPEED) ---
                                if False and self.reid_engine is not None and (self.frame_count % 3 == 0):
                                    person_crop = frame[y1:y2, x1:x2]
                                    embedding = self.reid_engine.extract_embedding(person_crop)
                                    global_id, is_new = self.reid_engine.identify_person(embedding)
                                    detections[-1]['global_id'] = global_id
                                    detections[-1]['is_new_person'] = is_new

                                    self._check_loitering(cx, cy, w, h, frame, alerts_this_frame)

                            elif cls_name in WEAPON_CLASSES:
                                if track_id is not None:
                                    current_track_ids.add(track_id)
                                    self.weapon_track_history[track_id] += 1

                                    if self.weapon_track_history[track_id] >= self.CONSENSUS_FRAMES:
                                        if track_id not in self.alerted_tracks:
                                            atype, sev, msg = WEAPON_CLASSES[cls_name]
                                            shot = self._screenshot(frame, f"WEAPON_{cls_name.upper().replace(' ', '_')}")
                                            alert = {
                                                'type': atype, 'message': msg, 'severity': sev,
                                                'screenshot': shot, 'timestamp': datetime.now().isoformat()
                                            }
                                            alerts_this_frame.append(alert)
                                            self._dispatch_alert(alert, shot)
                                            self.alerted_tracks.add(track_id)
                                else:
                                    key = f"weapon_{cls_name}"
                                    if self._can_alert(key, cooldown=20):
                                        shot = self._screenshot(frame, f"WEAPON_{cls_name.upper().replace(' ', '_')}")
                                        atype, sev, msg = WEAPON_CLASSES[cls_name]
                                        alert = {
                                            'type': atype, 'message': msg, 'severity': sev,
                                            'screenshot': shot, 'timestamp': datetime.now().isoformat()
                                        }
                                        alerts_this_frame.append(alert)
                                        self._dispatch_alert(alert, shot)

                    for tid in list(self.weapon_track_history.keys()):
                        if tid not in current_track_ids:
                            self.weapon_track_history[tid] = 0
                            if tid in self.alerted_tracks:
                                self.alerted_tracks.remove(tid)

                except Exception as e:
                    logger.error(f"⚠️ YOLO inference error: {e}")

            # 2. MediaPipe Pose Analysis (DISABLED FOR SPEED)
            if False and self.pose_detector and person_boxes and (self.frame_count % 3 == 0):  
                try:
                    pose_alerts = self._analyse_poses(frame, person_boxes, w, h, person_track_ids)
                    for pa in pose_alerts:
                        key = f"pose_{pa['type']}"
                        if self._can_alert(key, cooldown=15):
                            shot = self._screenshot(frame, pa['type'])
                            pa['screenshot'] = shot
                            alerts_this_frame.append(pa)
                            self._dispatch_alert(pa, shot)
                except Exception as e:
                    logger.error(f"⚠️ Pose analysis error: {e}")

            # 3. Motion / Fight Detection
            fight = self._detect_fight(frame, w, h)
            if fight:
                shot = self._screenshot(frame, "FIGHT")
                fight['screenshot'] = shot
                alerts_this_frame.append(fight)
                self._dispatch_alert(fight, shot)

            # 4. Rendering & Overlay
            display = self.nv.process(frame)
            annotated = self._draw(display, detections, alerts_this_frame)
            annotated = self.nv.draw_overlay(annotated)
            
            with self.lock:
                self.current_frame = annotated
                self.detection_results = detections
                
            time.sleep(0.01)

    def _dispatch_alert(self, alert: Dict[str, Any], shot: str) -> None:
        if self.logger_service:
            self.logger_service.log_alert(alert)
        if self.alerter_service:
            self.alerter_service.send_alert(alert, shot)

    def _analyse_poses(self, frame: np.ndarray, person_boxes: List[List[int]], w: int, h: int, person_track_ids: List[int] = None) -> List[Dict[str, Any]]:
        alerts = []
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        if person_track_ids is None:
            person_track_ids = [None] * len(person_boxes)

        for i, ((px1, py1, px2, py2), track_id) in enumerate(zip(person_boxes, person_track_ids)):
            slot = f"p{i}_{px1//80}"
            pad = 20
            cx1, cy1 = max(0, px1 - pad), max(0, py1 - pad)
            cx2, cy2 = min(w, px2 + pad), min(h, py2 + pad)
            crop = rgb[cy1:cy2, cx1:cx2]
            if crop.size == 0:
                continue
                
            result = self.pose_detector.process(crop)
            if not result.pose_landmarks:
                continue
                
            lm = result.pose_landmarks.landmark
            ch, cw = cy2 - cy1, cx2 - cx1
            
            def pt(idx: int) -> Tuple[float, float, float]:
                l = lm[idx]
                return (l.x * cw + cx1, l.y * ch + cy1, l.visibility)

            lw, rw = pt(LEFT_WRIST), pt(RIGHT_WRIST)
            le, re = pt(LEFT_ELBOW), pt(RIGHT_ELBOW)
            ls, rs = pt(LEFT_SHOULDER), pt(RIGHT_SHOULDER)
            lh, rh = pt(LEFT_HIP), pt(RIGHT_HIP)
            lk, rk = pt(LEFT_KNEE), pt(RIGHT_KNEE)
            nose = pt(NOSE)
            
            shoulder_y = (ls[1] + rs[1]) / 2
            hip_y = (lh[1] + rh[1]) / 2
            torso_h = abs(hip_y - shoulder_y) + 1e-6
            torso_cx = (ls[0] + rs[0] + lh[0] + rh[0]) / 4
            torso_cy = (shoulder_y + hip_y) / 2

            for wrist, side in [(lw, 'L'), (rw, 'R')]:
                if wrist[2] < 0.4:
                    continue
                dist = math.hypot(wrist[0] - torso_cx, wrist[1] - torso_cy)
                norm = dist / torso_h
                ckey = f"{slot}_wrist_{side}"
                if norm < 0.4:
                    self.wrist_counter[ckey] = self.wrist_counter.get(ckey, 0) + 1
                else:
                    self.wrist_counter[ckey] = 0
                    
                if self.wrist_counter.get(ckey, 0) >= 8:
                    self.wrist_counter[ckey] = 0
                    alerts.append({
                        'type': 'SELF_HARM_POSTURE', 'message': '🚨 Possible self-harm posture detected',
                        'severity': 'CRITICAL', 'timestamp': datetime.now().isoformat()
                    })

            # --- UPGRADE #2: Spatio-Temporal Kinematic Analysis ---
            raw_landmarks = [(l.x, l.y, l.z, l.visibility) for l in lm]
            
            if track_id is not None:
                action_result = self.temporal_analyzer.analyze(track_id, raw_landmarks)
                
                if action_result['action'] == 'AGGRESSIVE_STRIKE' and action_result['confidence'] > 0.6:
                    if self._can_alert(f"strike_{track_id}", cooldown=10):
                        alerts.append({
                            'type': 'VIOLENT_ACTIVITY', 
                            'message': f"🚨 Kinematic Strike Detected (Conf: {action_result['confidence']})",
                            'severity': 'CRITICAL', 
                            'timestamp': datetime.now().isoformat()
                        })

            if lk[2] > 0.4 and rk[2] > 0.4 and lh[2] > 0.4 and rh[2] > 0.4:
                knee_y = (lk[1] + rk[1]) / 2
                if hip_y > knee_y - 10:
                    if self._can_alert(f"collapse_{slot}", cooldown=30):
                        alerts.append({
                            'type': 'PERSON_COLLAPSED', 'message': '🚨 Person appears collapsed',
                            'severity': 'HIGH', 'timestamp': datetime.now().isoformat()
                        })

            if nose[2] > 0.4 and ls[2] > 0.4 and rs[2] > 0.4:
                if nose[1] > shoulder_y + torso_h * 0.3:
                    if self._can_alert(f"distress_{slot}", cooldown=30):
                        alerts.append({
                            'type': 'DISTRESS_POSTURE', 'message': '⚠️ Distress posture detected',
                            'severity': 'HIGH', 'timestamp': datetime.now().isoformat()
                        })
        return alerts

    def _check_loitering(self, cx: int, cy: int, w: int, h: int, frame: np.ndarray, alerts: List[Dict]) -> None:
        now = datetime.now()
        time_str = now.strftime("%H:%M")
        
        for zone_name, zone in self.zones.items():
            zx1 = int(zone['coords'][0] * w)
            zy1 = int(zone['coords'][1] * h)
            zx2 = int(zone['coords'][2] * w)
            zy2 = int(zone['coords'][3] * h)
            
            if zx1 <= cx <= zx2 and zy1 <= cy <= zy2:
                key = f"{zone_name}_{cx//50}_{cy//50}"
                if key not in self.person_tracker:
                    self.person_tracker[key] = now.timestamp()
                else:
                    duration = now.timestamp() - self.person_tracker[key]
                    threshold = zone['loiter_threshold']
                    after_hrs = time_str >= zone.get('restricted_after', '23:59')
                    effective = threshold // 3 if after_hrs else threshold
                    
                    if duration > effective:
                        akey = f"loiter_{zone_name}"
                        if self._can_alert(akey, cooldown=120):
                            tag = " (AFTER HOURS)" if after_hrs else ""
                            shot = self._screenshot(frame, f"LOITER_{zone_name.replace(' ', '_')}")
                            alert = {
                                'type': 'LOITERING',
                                'message': f"🚨 Loitering in {zone_name}{tag} — {int(duration)}s",
                                'severity': 'HIGH' if after_hrs else 'MEDIUM',
                                'zone': zone_name, 'duration': int(duration),
                                'screenshot': shot, 'timestamp': now.isoformat()
                            }
                            alerts.append(alert)
                            self._dispatch_alert(alert, shot)
                            self.person_tracker[key] = now.timestamp()

    def _detect_fight(self, frame: np.ndarray, w: int, h: int) -> Optional[Dict[str, Any]]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)
        
        if self.prev_frame_gray is None:
            self.prev_frame_gray = gray
            return None
            
        diff = cv2.absdiff(self.prev_frame_gray, gray)
        _, thr = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
        score = np.sum(thr) / (w * h * 255)
        self.motion_history.append(score)
        self.prev_frame_gray = gray
        
        if len(self.motion_history) >= 10:
            avg = np.mean(self.motion_history)
            var = np.var(self.motion_history)
            if avg > self.fight_threshold and var > 0.001:
                if self._can_alert("fight", cooldown=60):
                    return {
                        'type': 'VIOLENT_ACTIVITY',
                        'message': '🚨 VIOLENT ACTIVITY DETECTED — Immediate action!',
                        'severity': 'CRITICAL',
                        'motion_score': round(float(avg), 3),
                        'timestamp': datetime.now().isoformat()
                    }
        return None

    def _draw(self, frame: np.ndarray, detections: List[Dict[str, Any]], alerts: List[Dict]) -> np.ndarray:
        h, w = frame.shape[:2]
        
        for det in detections:
            x1, y1, x2, y2 = det['bbox']
            cls = det['class']
            conf = det['confidence']
            track_id = det.get('track_id')
            
            if cls in WEAPON_CLASSES:
                color = (255, 0, 255) 
            elif cls == 'person':
                color = (0, 255, 100)
            else:
                color = (200, 200, 0)
                
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            
            label = f"{cls} {conf:.0%}"
            if track_id is not None:
                label += f" | ID:{track_id}"
                global_id = det.get('global_id')
                if global_id is not None:
                    label += f" [G:{global_id}]"
            
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(frame, (x1, y1 - th - 6), (x1 + tw + 6, y1), color, -1)
            cv2.putText(frame, label, (x1 + 3, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            
        cv2.putText(frame, f"FPS: {self.fps:.1f}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        cv2.putText(frame, f"{'GPU' if self.device == 'cuda' else 'CPU'}: {self.device.upper()}", (10, 80),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (50, 220, 50) if self.device == 'cuda' else (100, 100, 255), 1)
        cv2.putText(frame, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), (w - 220, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)
        
        if alerts:
            sev = alerts[-1].get('severity', 'MEDIUM')
            col = (0, 0, 180) if sev == 'CRITICAL' else (0, 100, 200)
            cv2.rectangle(frame, (0, h - 44), (w, h), col, -1)
            cv2.putText(frame, alerts[-1]['message'][:80], (10, h - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
            
        return frame

    def _demo_loop(self) -> None:
        demo = np.zeros((480, 640, 3), dtype=np.uint8)
        i = 0
        cols = [(255, 100, 100), (100, 255, 100), (100, 100, 255)]
        
        while self.running:
            f = demo.copy()
            cv2.putText(f, "DEMO MODE — No Camera", (120, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
            cv2.putText(f, datetime.now().strftime('%H:%M:%S'), (260, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.7, cols[i % 3], 1)
            cv2.rectangle(f, (20, 20), (620, 460), cols[i % 3], 2)
            i += 1
            
            with self.lock:
                self.current_frame = f
                self.fps = 30.0
            time.sleep(0.033)

    def _can_alert(self, key: str, cooldown: int = 60) -> bool:
        now = time.time()
        if now - self.last_alert_time.get(key, 0) > cooldown:
            self.last_alert_time[key] = now
            return True
        return False

    def _screenshot(self, frame: np.ndarray, label: str) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{label}_{ts}.jpg"
        cv2.imwrite(f"static/screenshots/{filename}", frame)
        return filename

    def generate_frames(self):
        while True:
            with self.lock:
                frame = self.current_frame
                
            if frame is None:
                blank = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(blank, "Camera Starting...", (150, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (100, 100, 100), 2)
                frame = blank
                
            ret, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            if not ret:
                time.sleep(0.02)
                continue
                
            yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')
            time.sleep(0.02)

    def get_status(self) -> Dict[str, Any]:
        return {
            'running': self.running,
            'fps': round(self.fps, 1),
            'detections': len(self.detection_results),
            'objects': [d['class'] for d in self.detection_results],
            'pose_active': self.pose_detector is not None,
            'device': self.device,
            'gpu_available': GPU_AVAILABLE
        }

    def get_zones(self) -> Dict[str, Dict[str, Any]]:
        return self.zones

    def update_zones(self, zones_data: Dict[str, Dict[str, Any]]) -> None:
        self.zones = zones_data

    def switch_device(self, device: str) -> Dict[str, Any]:
        device = device.lower()
        if device not in ['cpu', 'cuda']:
            return {'success': False, 'error': 'Invalid device'}
        if device == 'cuda' and not GPU_AVAILABLE:
            return {'success': False, 'error': 'No GPU available on this machine'}
            
        self.device = device
        if self.model:
            try:
                self.model.to(device)
                logger.info(f"✅ Switched AI device to {device.upper()}")
            except Exception as e:
                return {'success': False, 'error': str(e)}
        return {'success': True, 'device': device}

    def get_device_info(self) -> Dict[str, Any]:
        return {
            'current': self.device,
            'gpu_available': GPU_AVAILABLE,
            'gpu_name': GPU_NAME,
            'gpu_vram': GPU_VRAM,
            'fps': round(self.fps, 1)
        }

    def set_night_vision(self, mode: str) -> None:
        self.nv.set_mode(mode)

    def get_night_vision(self) -> Dict[str, Any]:
        return {
            'mode': self.nv.get_mode(),
            'brightness': self.nv.current_brightness,
            'auto_active': self.nv.auto_active
        }