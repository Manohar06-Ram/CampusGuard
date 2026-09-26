"""
CampusGuard — Multi-Camera Manager
Handles multiple simultaneous CCTV streams and AI detectors.
"""
import threading
import logging
import cv2
from utils.detector import SecurityDetector

logger = logging.getLogger("CampusGuard.CameraManager")

class CameraManager:
    def __init__(self, alerter_service=None, logger_service=None):
        self.active_cameras = {}
        self.manager_lock = threading.Lock()
        self.alerter_service = alerter_service   # <-- ADDED
        self.logger_service = logger_service     # <-- ADDED

    def register_camera(self, cam_id: str, source, name: str):
        """Register and start a new camera stream."""
        with self.manager_lock:
            if cam_id in self.active_cameras:
                logger.warning(f"Camera {cam_id} is already running.")
                return

            logger.info(f"🎥 Initializing Camera: {name} ({cam_id})...")
            
            detector = SecurityDetector(source=source, camera_id=cam_id)
            
            # 🔌 CRITICAL FIX: Wire the services to the detector!
            detector.set_services(self.logger_service, self.alerter_service)
            
            detector.start()

            self.active_cameras[cam_id] = {
                'detector': detector,
                'name': name,
                'status': 'ACTIVE'
            }
            logger.info(f"✅ Camera {cam_id} ({name}) is now LIVE.")

    def stop_camera(self, cam_id: str):
        with self.manager_lock:
            if cam_id in self.active_cameras:
                cam_data = self.active_cameras[cam_id]
                cam_data['detector'].stop()
                del self.active_cameras[cam_id]
                logger.info(f"🛑 Camera {cam_id} stopped.")

    def get_frame(self, cam_id: str):
        with self.manager_lock:
            if cam_id not in self.active_cameras:
                return None
            detector = self.active_cameras[cam_id]['detector']
        
        with detector.lock:
            if detector.current_frame is not None:
                ret, buffer = cv2.imencode('.jpg', detector.current_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ret:
                    return buffer.tobytes()
        return None

    def get_all_cameras(self):
        with self.manager_lock:
            return [
                {'id': cam_id, 'name': data['name'], 'status': data['status']}
                for cam_id, data in self.active_cameras.items()
            ]