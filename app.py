"""
CampusGuard — Main Flask Application v2
Production-ready web server with security headers, graceful shutdown, and service injection.
"""

import os
import sys
import atexit
import logging
from datetime import datetime
from functools import wraps

import cv2
from flask import Flask, render_template, redirect, url_for, request, jsonify, Response
from utils.camera_manager import CameraManager
from flask import Flask, render_template, request, jsonify, Response, redirect, url_for, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user

# Import internal modules
from utils.auth import User, init_users, verify_user, _load_users
from utils.detector import SecurityDetector
from utils.alert_manager import AlertManager
from utils.logger import SecurityLogger

# Configure application logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("CampusGuard.App")

# ── Application Initialization ────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'campusguard_secret_2026_change_in_production')

# Flask-Login Setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "Please log in to access the security dashboard."

@login_manager.user_loader
def load_user(user_id: str):
    return User.get(user_id)

# Initialize Core Services
init_users()
detector = SecurityDetector()
alert_mgr = AlertManager()
app_logger = SecurityLogger()

# 🚀 CRITICAL: Inject services into detector to prevent circular imports 
# and enable high-performance, thread-safe alert dispatching.
detector.set_services(logger_service=app_logger, alerter_service=alert_mgr)
# Connect the alerter service to the detector
if 'alerter' in globals():
    detector.set_services(logger_service, alerter)

# ── Security & Middleware ─────────────────────────────────────────────────────

@app.after_request
def add_security_headers(response):
    """Inject security headers to protect against common web vulnerabilities."""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response

# ── Graceful Shutdown ─────────────────────────────────────────────────────────

def cleanup():
    """Ensures clean shutdown of camera threads and flushes logs to disk."""
    logger.info("🛑 Shutting down CampusGuard gracefully...")
    detector.stop()
    app_logger.flush_to_disk()
    logger.info("✅ Cleanup complete. Goodbye!")

atexit.register(cleanup)

   # Initialize Multi-Camera Manager
   # Pass the alerter and logger to the camera manager so detectors can use them
camera_manager = CameraManager(alerter_service=alert_mgr, logger_service=app_logger)

   # --- DEFINE YOUR CAMERAS HERE ---
   # For testing: CAM-01 is your webcam (0). CAM-02 is a dummy RTSP stream.
   # In a real college, these would be RTSP URLs like 'rtsp://192.168.1.50:554/stream'
CAMERAS_CONFIG = [
       {'id': 'CAM-01', 'source': 0, 'name': 'Main Gate'},
       {'id': 'CAM-02', 'source': 1, 'name': 'Library Entrance'}, # Uses 2nd webcam if available
       # {'id': 'CAM-03', 'source': 'rtsp://admin:password@192.168.1.100:554', 'name': 'Parking Lot'}
   ]

# ── Authentication Routes ─────────────────────────────────────────────────────

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        username = str(data.get('username', '')).strip()
        password = str(data.get('password', '')).strip()
        
        if not username or not password:
            return jsonify({'success': False, 'message': 'Username and password are required.'})
            
        user, msg = verify_user(username, password, ip_address=request.remote_addr)
        
        if user:
            login_user(user, remember=False)
            app_logger.log_login(username, request.remote_addr, success=True)
            logger.info(f"✅ Login successful: {username}")
            return jsonify({'success': True, 'redirect': url_for('dashboard')})
            
        app_logger.log_login(username, request.remote_addr, success=False)
        logger.warning(f"⚠️ Login failed: {username} - {msg}")
        return jsonify({'success': False, 'message': msg})
        
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    username = current_user.username
    app_logger.log_logout(username)
    logout_user()
    logger.info(f"👋 Logged out: {username}")
    return redirect(url_for('login'))

# ── Dashboard Routes ──────────────────────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html', user=current_user)

# ── API: Alerts & Statistics ──────────────────────────────────────────────────

@app.route('/api/alerts')
@login_required
def get_alerts():
    try:
        return jsonify(app_logger.get_recent_alerts(50))
    except Exception as e:
        logger.error(f"Error fetching alerts: {e}")
        return jsonify({'error': 'Failed to fetch alerts'}), 500

@app.route('/api/stats')
@login_required
def get_stats():
    try:
        return jsonify(app_logger.get_stats())
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        return jsonify({'error': 'Failed to fetch stats'}), 500

@app.route('/api/acknowledge', methods=['POST'])
@login_required
def acknowledge_alert():
    try:
        data = request.get_json(silent=True) or {}
        alert_id = str(data.get('alert_id', '')).strip()
        if not alert_id:
            return jsonify({'success': False, 'error': 'Missing alert_id'})
            
        result = alert_mgr.acknowledge(alert_id, current_user.username)
        return jsonify({'success': result})
    except Exception as e:
        logger.error(f"Error acknowledging alert: {e}")
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

@app.route('/api/send_test_alert', methods=['POST'])
@login_required
def send_test_alert():
    try:
        return jsonify(alert_mgr.send_test_alert())
    except Exception as e:
        logger.error(f"Error sending test alert: {e}")
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

# ── API: Camera & Detection ───────────────────────────────────────────────────

@app.route('/video_feed/<cam_id>')
def video_feed(cam_id):
       def generate():
           while True:
               frame = camera_manager.get_frame(cam_id)
               if frame is not None:
                   yield (b'--frame\r\n'
                          b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
               else:
                   # Send a blank black frame if camera is offline
                   import numpy as np
                   blank = np.zeros((480, 640, 3), dtype=np.uint8)
                   cv2.putText(blank, f"Initializing {cam_id}...", (120, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (255,255,255), 2)
                   ret, buffer = cv2.imencode('.jpg', blank)
                   yield (b'--frame\r\n'
                          b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
                   import time
                   time.sleep(0.1)
                   
       return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/cameras')
def get_cameras():
       return jsonify(camera_manager.get_all_cameras())
   
@app.route('/api/detection_status')
@login_required
def detection_status():
    try:
        return jsonify(detector.get_status())
    except Exception as e:
        logger.error(f"Error fetching detection status: {e}")
        return jsonify({'error': 'Failed to fetch status'}), 500

@app.route('/api/start_camera', methods=['POST'])
@login_required
def start_camera():
    try:
        # Find CAM-01 in our config and start it via the Camera Manager
        cam = next((c for c in CAMERAS_CONFIG if c['id'] == 'CAM-01'), None)
        if cam and 'CAM-01' not in camera_manager.active_cameras:
            camera_manager.register_camera(cam['id'], cam['source'], cam['name'])
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error starting camera: {e}")
        return jsonify({'success': False, 'error': 'Failed to start camera'}), 500

@app.route('/api/stop_camera', methods=['POST'])
@login_required
def stop_camera():
    try:
        detector.stop()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error stopping camera: {e}")
        return jsonify({'success': False, 'error': 'Failed to stop camera'}), 500
@app.route('/api/screenshot', methods=['POST'])
@login_required
def take_screenshot():
    try:
        import cv2
        from datetime import datetime
        
        # Safely grab the current frame from the camera thread
        with detector.lock:
            frame = detector.current_frame
            
        if frame is None:
            return jsonify({'success': False, 'error': 'Camera is not active'}), 400

        # Create filename and save
        os.makedirs('static/screenshots', exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"MANUAL_{ts}.jpg"
        path = os.path.join('static', 'screenshots', filename)
        
        cv2.imwrite(path, frame) 
        
        return jsonify({'success': True, 'filename': filename})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
# ── API: Screenshots & Evidence ───────────────────────────────────────────────

@app.route('/api/screenshots')
@login_required
def get_screenshots():
    try:
        return jsonify(app_logger.get_screenshots(limit=50))
    except Exception as e:
        logger.error(f"Error fetching screenshots: {e}")
        return jsonify({'error': 'Failed to fetch screenshots'}), 500

@app.route('/api/screenshots/delete', methods=['POST'])
@login_required
def delete_screenshot():
    try:
        data = request.get_json(silent=True) or {}
        fn = str(data.get('filename', '')).strip()
        
        # 🛡️ Security: Prevent directory traversal attacks
        if any(c in fn for c in ['/', '\\', '..']) or not fn.endswith('.jpg'):
            logger.warning(f"Blocked malicious filename attempt: {fn}")
            return jsonify({'success': False, 'error': 'Invalid filename'}), 400
            
        path = os.path.join('static', 'screenshots', fn)
        if os.path.exists(path):
            os.remove(path)
            logger.info(f"🗑️ Deleted screenshot: {fn}")
            return jsonify({'success': True})
            
        return jsonify({'success': False, 'error': 'File not found'}), 404
    except Exception as e:
        logger.error(f"Error deleting screenshot: {e}")
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

@app.route('/api/screenshots/delete_all', methods=['POST'])
@login_required
def delete_all_screenshots():
    try:
        folder = os.path.join('static', 'screenshots')
        count = 0
        if os.path.exists(folder):
            for f in os.listdir(folder):
                if f.endswith('.jpg'):
                    os.remove(os.path.join(folder, f))
                    count += 1
        logger.info(f"🗑️ Deleted {count} screenshots.")
        return jsonify({'success': True, 'deleted': count})
    except Exception as e:
        logger.error(f"Error deleting all screenshots: {e}")
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

# ── API: Configuration (Zones, Night Vision, Device) ─────────────────────────

@app.route('/api/zones', methods=['GET'])
@login_required
def get_zones():
    return jsonify(detector.get_zones())

@app.route('/api/zones', methods=['POST'])
@login_required
def update_zones():
    try:
        detector.update_zones(request.get_json(silent=True) or {})
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error updating zones: {e}")
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

@app.route('/api/night_vision', methods=['GET'])
@login_required
def get_night_vision():
    return jsonify(detector.get_night_vision())

@app.route('/api/night_vision', methods=['POST'])
@login_required
def set_night_vision():
    try:
        data = request.get_json(silent=True) or {}
        mode = str(data.get('mode', 'NORMAL')).upper()
        if mode not in ['NORMAL', 'NIGHT', 'THERMAL', 'AUTO']:
            return jsonify({'success': False, 'error': 'Invalid mode'}), 400
            
        detector.set_night_vision(mode)
        return jsonify({'success': True, 'mode': mode})
    except Exception as e:
        logger.error(f"Error setting night vision: {e}")
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

@app.route('/api/device', methods=['GET'])
@login_required
def get_device():
    return jsonify(detector.get_device_info())

@app.route('/api/device', methods=['POST'])
@login_required
def switch_device():
    try:
        data = request.get_json(silent=True) or {}
        device = str(data.get('device', 'cpu')).lower()
        return jsonify(detector.switch_device(device))
    except Exception as e:
        logger.error(f"Error switching device: {e}")
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

# ── API: Admin Only ───────────────────────────────────────────────────────────

@app.route('/api/users')
@login_required
def list_users_api():
    if current_user.role != 'admin':
        logger.warning(f"Unauthorized user list access attempt by: {current_user.username}")
        return jsonify({'error': 'Admin access required'}), 403
        
    try:
        users = _load_users()
        safe_users = [{
            'id': uid, 
            'username': d['username'], 
            'role': d['role'],
            'email': d.get('email', ''),
            'created_at': d.get('created_at', ''),
            'locked': bool(d.get('permanent_lock') or d.get('locked_until'))
        } for uid, d in users.items()]
        return jsonify(safe_users)
    except Exception as e:
        logger.error(f"Error fetching users: {e}")
        return jsonify({'error': 'Internal server error'}), 500

# ── Main Execution ────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("\n" + "="*60)
    print("  🛡️  CampusGuard Security System v2.0 — YOLO26")
    print("  🌐 Dashboard: http://localhost:5000")
    print("  🔑 Default Login: admin / Admin@123")
    print("  ⚙️  Manage Users: python manage.py")
    print("  📱 Twilio Setup: python twilio_setup.py")
    print("="*60 + "\n")
    
    # threaded=True is required for concurrent video streaming and background detection
    app.run(debug=False, threaded=True, host='0.0.0.0', port=5000)