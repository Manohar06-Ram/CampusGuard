"""
CampusGuard — Authentication & User Management v2
Secure, thread-safe user authentication with brute-force protection and audit logging.
"""

import bcrypt
import json
import os
import threading
import logging
from flask_login import UserMixin
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, Any

# Configure professional logging
logger = logging.getLogger("CampusGuard.Auth")

# Thread lock for file operations to prevent race conditions
file_lock = threading.Lock()

USERS_FILE = 'data/users.json'
LOGIN_LOG_FILE = 'data/login_log.json'

MAX_ATTEMPTS = 5
LOCK_MINUTES = 30
MAX_LOCKOUTS = 3
MIN_PASSWORD_LENGTH = 8  # Increased from 6 for better security


class User(UserMixin):
    def __init__(self, user_id: str, username: str, role: str, email: str):
        self.id = user_id
        self.username = username
        self.role = role
        self.email = email

    @staticmethod
    def get(user_id: str) -> Optional['User']:
        users = _load_users()
        user_data = users.get(user_id)
        if user_data:
            return User(
                user_id, 
                user_data['username'], 
                user_data['role'], 
                user_data.get('email', '')
            )
        return None


def _load_users() -> Dict[str, Any]:
    with file_lock:
        if not os.path.exists(USERS_FILE):
            return {}
        try:
            with open(USERS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            logger.error("⚠️ Corrupted users.json detected. Resetting to empty dict to prevent crash.")
            return {}
        except Exception as e:
            logger.error(f"Failed to load users: {e}")
            return {}


def _save_users(users: Dict[str, Any]) -> None:
    with file_lock:
        try:
            os.makedirs(os.path.dirname(USERS_FILE), exist_ok=True)
            with open(USERS_FILE, 'w', encoding='utf-8') as f:
                json.dump(users, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save users: {e}")


def _log_login_event(username: str, ip_address: str, success: bool) -> None:
    """Logs login attempts for security auditing."""
    with file_lock:
        try:
            log_data = []
            if os.path.exists(LOGIN_LOG_FILE):
                with open(LOGIN_LOG_FILE, 'r', encoding='utf-8') as f:
                    try:
                        log_data = json.load(f)
                    except json.JSONDecodeError:
                        log_data = []
            
            log_data.insert(0, {
                'username': username,
                'ip': ip_address,
                'success': success,
                'timestamp': datetime.now().isoformat()
            })
            
            # Keep log size manageable (max 500 entries)
            if len(log_data) > 500:
                log_data = log_data[:500]
                
            with open(LOGIN_LOG_FILE, 'w', encoding='utf-8') as f:
                json.dump(log_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to log login event: {e}")


def init_users() -> None:
    """Initializes default admin and security accounts if no users exist."""
    os.makedirs('data', exist_ok=True)
    users = _load_users()
    
    if not users:
        logger.info("Initializing default users...")
        h1 = bcrypt.hashpw('Admin@123'.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        h2 = bcrypt.hashpw('Security@123'.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        users['user_001'] = {
            'username': 'admin', 
            'password': h1, 
            'role': 'admin',
            'email': 'admin@campus.edu', 
            'created_at': datetime.now().isoformat(),
            'failed_attempts': 0, 
            'locked_until': None, 
            'lockout_count': 0
        }
        users['user_002'] = {
            'username': 'security', 
            'password': h2, 
            'role': 'officer',
            'email': 'security@campus.edu', 
            'created_at': datetime.now().isoformat(),
            'failed_attempts': 0, 
            'locked_until': None, 
            'lockout_count': 0
        }
        _save_users(users)
        logger.info("✅ Default users created: admin/Admin@123 | security/Security@123")


def verify_user(username: str, password: str, ip_address: str = 'unknown') -> Tuple[Optional[User], str]:
    """
    Verifies user credentials with brute-force protection.
    Returns (User object, success_message) or (None, error_message).
    """
    users = _load_users()
    uname_target = username.lower().strip()
    user_id = None
    user_data = None

    # Find user by username or email
    for uid, data in users.items():
        if data.get('username', '').lower() == uname_target or data.get('email', '').lower() == uname_target:
            user_id = uid
            user_data = data
            break

    # Prevent timing attacks by hashing a dummy password if user not found
    if not user_data:
        bcrypt.checkpw(b'dummy_password', bcrypt.gensalt())
        _log_login_event(username, ip_address, False)
        return None, "Invalid username or password."

    # Check permanent lock
    if user_data.get('permanent_lock'):
        _log_login_event(username, ip_address, False)
        return None, "Account permanently locked. Contact the system administrator."

    # Check temporary lock
    if user_data.get('locked_until'):
        try:
            lock_time = datetime.fromisoformat(user_data['locked_until'])
            if datetime.now() < lock_time:
                remaining = int((lock_time - datetime.now()).total_seconds() / 60) + 1
                _log_login_event(username, ip_address, False)
                return None, f"Account temporarily locked. Try again in {remaining} minute(s)."
            else:
                # Lock expired, reset attempts
                user_data['locked_until'] = None
                user_data['failed_attempts'] = 0
        except ValueError:
            logger.error(f"Invalid date format in locked_until for user {user_id}")

    # Verify password
    try:
        if bcrypt.checkpw(password.encode('utf-8'), user_data['password'].encode('utf-8')):
            # Success
            user_data['failed_attempts'] = 0
            user_data['last_login'] = datetime.now().isoformat()
            users[user_id] = user_data
            _save_users(users)
            _log_login_event(username, ip_address, True)
            logger.info(f"✅ Successful login: {username} from {ip_address}")
            return User(user_id, user_data['username'], user_data['role'], user_data.get('email', '')), "Success"
    except Exception as e:
        logger.error(f"Password verification error for {username}: {e}")

    # Failure
    user_data['failed_attempts'] = user_data.get('failed_attempts', 0) + 1
    attempts = user_data['failed_attempts']

    if attempts >= MAX_ATTEMPTS:
        lockout_count = user_data.get('lockout_count', 0) + 1
        user_data['lockout_count'] = lockout_count
        user_data['failed_attempts'] = 0
        
        if lockout_count >= MAX_LOCKOUTS:
            user_data['permanent_lock'] = True
            users[user_id] = user_data
            _save_users(users)
            _log_login_event(username, ip_address, False)
            logger.warning(f"🚨 Account permanently locked due to max lockouts: {username}")
            return None, "Account permanently locked due to repeated violations. Contact administrator."
        
        lock_until = datetime.now() + timedelta(minutes=LOCK_MINUTES)
        user_data['locked_until'] = lock_until.isoformat()
        users[user_id] = user_data
        _save_users(users)
        _log_login_event(username, ip_address, False)
        logger.warning(f"⚠️ Account temporarily locked for {LOCK_MINUTES} mins: {username}")
        return None, f"Too many failed attempts. Account locked for {LOCK_MINUTES} minutes."

    # Update failed attempts without locking yet
    users[user_id] = user_data
    _save_users(users)
    _log_login_event(username, ip_address, False)
    remaining = MAX_ATTEMPTS - attempts
    return None, f"Invalid username or password. {remaining} attempt(s) remaining."


def create_user(username: str, password: str, role: str, email: str, created_by: str = 'admin') -> Tuple[bool, str]:
    """Creates a new user with validation."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return False, f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
    
    if role not in ['admin', 'officer', 'viewer']:
        return False, "Invalid role specified."

    users = _load_users()
    uname_target = username.lower().strip()
    email_target = email.lower().strip() if email else ""

    for data in users.values():
        if data.get('username', '').lower() == uname_target:
            return False, "Username already exists."
        if email_target and data.get('email', '').lower() == email_target:
            return False, "Email address already exists."

    uid = f"user_{str(len(users) + 1).zfill(3)}"
    hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    users[uid] = {
        'username': username,
        'password': hashed,
        'role': role,
        'email': email,
        'created_at': datetime.now().isoformat(),
        'failed_attempts': 0,
        'locked_until': None,
        'lockout_count': 0,
        'created_by': created_by
    }
    
    _save_users(users)
    logger.info(f"✅ New user created: {username} (Role: {role}) by {created_by}")
    return True, "User created successfully."