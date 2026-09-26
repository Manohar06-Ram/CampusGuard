"""
CampusGuard — Security Logger & Stats Engine v2
High-performance, thread-safe, memory-cached logging system.
"""

import json
import os
import re
import threading
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from typing import List, Dict, Any, Optional

# Configure professional logging
logger = logging.getLogger("CampusGuard.Logger")

# Thread lock for file operations
file_lock = threading.Lock()

ALERTS_FILE = 'data/alerts.json'
LOGIN_LOG_FILE = 'data/login_log.json'
SCREENSHOTS_DIR = 'static/screenshots'

# Limits to prevent unbounded memory/disk growth
MAX_ALERTS = 2000
MAX_LOGIN_LOGS = 1000


class SecurityLogger:
    def __init__(self):
        os.makedirs('data', exist_ok=True)
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
        
        # 🚀 PERFORMANCE: Load data into memory once to avoid constant disk I/O
        self._alerts_cache: List[Dict[str, Any]] = self._load_json(ALERTS_FILE, [])
        self._login_cache: List[Dict[str, Any]] = self._load_json(LOGIN_LOG_FILE, [])
        
        logger.info(f"✅ Logger initialized. Loaded {len(self._alerts_cache)} alerts and {len(self._login_cache)} login records into memory.")

    def _load_json(self, path: str, default: Any) -> Any:
        """Safely loads JSON with auto-healing for corrupted files."""
        if not os.path.exists(path):
            return default
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            logger.error(f"⚠️ Corrupted JSON detected: {path}. Resetting to default.")
            return default
        except Exception as e:
            logger.error(f"Failed to load {path}: {e}")
            return default

    def _save_json(self, path: str, data: Any) -> None:
        """Thread-safe JSON save with error handling."""
        with file_lock:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.error(f"Failed to save {path}: {e}")

    def flush_to_disk(self) -> None:
        """Manually sync memory cache to disk (useful before shutdown)."""
        self._save_json(ALERTS_FILE, self._alerts_cache)
        self._save_json(LOGIN_LOG_FILE, self._login_cache)
        logger.info("💾 Logger cache flushed to disk.")

    # ── Alert Logging ─────────────────────────────────────────────────────────

    def log_alert(self, alert: Dict[str, Any]) -> None:
        """Logs an alert to memory and periodically to disk."""
        entry = {
            'id': f"alert_{len(self._alerts_cache) + 1}_{int(datetime.now().timestamp())}",
            'type': alert.get('type', 'UNKNOWN'),
            'message': alert.get('message', ''),
            'severity': alert.get('severity', 'MEDIUM'),
            'zone': alert.get('zone', ''),
            'screenshot': alert.get('screenshot', ''),
            'timestamp': alert.get('timestamp', datetime.now().isoformat())
        }
        
        self._alerts_cache.append(entry)
        
        # Trim cache to prevent memory leaks
        if len(self._alerts_cache) > MAX_ALERTS:
            self._alerts_cache = self._alerts_cache[-MAX_ALERTS:]
            
        # Save to disk immediately for critical alerts, otherwise batch later
        if entry.get('severity') == 'CRITICAL':
            self._save_json(ALERTS_FILE, self._alerts_cache)
            logger.critical(f" CRITICAL Alert Logged: {entry['type']}")
        else:
            # For non-critical, we could batch saves, but for simplicity we save here
            # In a production app, you'd use a background thread to save every N seconds.
            self._save_json(ALERTS_FILE, self._alerts_cache)

    # ── Login Logging ─────────────────────────────────────────────────────────

    def log_login(self, username: str, ip: str, success: bool = True) -> None:
        entry = {
            'username': username,
            'ip': ip,
            'success': success,
            'timestamp': datetime.now().isoformat()
        }
        self._login_cache.append(entry)
        if len(self._login_cache) > MAX_LOGIN_LOGS:
            self._login_cache = self._login_cache[-MAX_LOGIN_LOGS:]
        self._save_json(LOGIN_LOG_FILE, self._login_cache)
        
        status = "Success" if success else "Failed"
        logger.info(f"🔐 Login {status}: {username} from {ip}")

    def log_logout(self, username: str) -> None:
        entry = {
            'username': username,
            'action': 'logout',
            'timestamp': datetime.now().isoformat()
        }
        self._login_cache.append(entry)
        self._save_json(LOGIN_LOG_FILE, self._login_cache)
        logger.info(f"👋 Logout: {username}")

    # ── Data Retrieval ────────────────────────────────────────────────────────

    def get_recent_alerts(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns the most recent alerts from memory cache."""
        return list(reversed(self._alerts_cache[-limit:]))

    def get_stats(self) -> Dict[str, Any]:
        """Calculates statistics efficiently from memory cache."""
        now = datetime.now()
        today = now.date().isoformat()
        week_ago = (now - timedelta(days=7)).isoformat()

        critical = sum(1 for a in self._alerts_cache if a.get('severity') == 'CRITICAL')
        today_c = sum(1 for a in self._alerts_cache if a.get('timestamp', '')[:10] == today)

        # 7-day chart data
        day_counts = defaultdict(int)
        for a in self._alerts_cache:
            ts = a.get('timestamp', '')
            if ts >= week_ago:
                day_counts[ts[:10]] += 1

        chart_labels = []
        chart_values = []
        for i in range(6, -1, -1):
            day = (now - timedelta(days=i)).date().isoformat()
            chart_labels.append(day)
            chart_values.append(day_counts.get(day, 0))

        # Alert type breakdown
        type_counts = defaultdict(int)
        for a in self._alerts_cache:
            type_counts[a.get('type', 'UNKNOWN')] += 1

        return {
            'total': len(self._alerts_cache),
            'critical': critical,
            'today': today_c,
            'chart_labels': chart_labels,
            'chart_values': chart_values,
            'type_breakdown': dict(type_counts)
        }

    def get_screenshots(self, limit: int = 50) -> List[Dict[str, str]]:
        """Retrieves screenshot metadata with robust filename parsing."""
        screenshots = []
        if not os.path.exists(SCREENSHOTS_DIR):
            return screenshots
            
        # Regex to extract label from filenames like "WEAPON_KNIFE_20260913_094440.jpg"
        # or "FIGHT_20260913_094310.jpg"
        pattern = re.compile(r'^([A-Z_]+?)_\d{8}_\d{6}\.jpg$')
        
        files = sorted(os.listdir(SCREENSHOTS_DIR), reverse=True)
        
        for f in files:
            if f.lower().endswith('.jpg'):
                match = pattern.match(f)
                label = match.group(1) if match else f.split('_')[0]
                
                screenshots.append({
                    'filename': f,
                    'label': label,
                    'url': f'/static/screenshots/{f}'
                })
                
            if len(screenshots) >= limit:
                break
                
        return screenshots