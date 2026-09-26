"""
CampusGuard — Admin Management Tool
Only YOU (the developer/installer) run this.
Clients never see or run this file.

Usage:
  python manage.py
  python manage.py list-users
  python manage.py add-user
"""

import bcrypt
import json
import os
import sys
import getpass
import re
from datetime import datetime

USERS_FILE = 'data/users.json'
MIN_PASSWORD_LENGTH = 8  # Synced with auth.py security standards

# Terminal Colors
G = '\033[92m'   # Green
R = '\033[91m'   # Red
Y = '\033[93m'   # Yellow
B = '\033[94m'   # Blue
W = '\033[0m'    # Reset
BOLD = '\033[1m'

def safe_load_users():
    """Safely loads users with auto-healing for corrupted JSON."""
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError:
        print(f"{R}⚠️  Warning: users.json is corrupted. Resetting to empty.{W}")
        return {}
    except Exception as e:
        print(f"{R}⚠️  Error loading users: {e}{W}")
        return {}

def save_users(users):
    """Safely saves users to disk."""
    try:
        os.makedirs(os.path.dirname(USERS_FILE), exist_ok=True)
        with open(USERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(users, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"{R}❌ Error saving users: {e}{W}")

def banner():
    print(f"\n{BOLD}{B}{'='*60}")
    print(f"   🛡️  CampusGuard — Admin Management Tool")
    print(f"   ⚠️  WARNING: Only run this as the system administrator")
    print(f"{'='*60}{W}\n")

def cmd_list():
    users = safe_load_users()
    if not users:
        print(f"{Y}ℹ️  No users found in the system.{W}\n")
        return
    
    print(f"\n{BOLD}{'ID':<12} {'Username / Email':<32} {'Role':<10} {'Status'}{W}")
    print("-" * 75)
    
    for uid, d in users.items():
        username = d.get('username', 'Unknown')
        role = d.get('role', 'unknown').upper()
        
        # Determine status
        if d.get('permanent_lock'):
            status = f"{R}[PERMANENTLY LOCKED]{W}"
        elif d.get('locked_until'):
            status = f"{Y}[TEMPORARILY LOCKED]{W}"
        else:
            status = f"{G}[Active]{W}"
            
        print(f"{uid:<12} {username:<32} {role:<10} {status}")
        
    print(f"\n{G}✅ Total: {len(users)} user(s){W}\n")

def cmd_add():
    print(f"\n{BOLD}➕ Add New User{W}\n{'-'*40}")
    users = safe_load_users()
    
    # Username / Email input
    username = input("Username (or Email address): ").strip()
    if not username:
        print(f"{R}❌ Username cannot be empty.{W}")
        return
        
    # Basic email validation if it contains '@'
    if '@' in username and not re.match(r"[^@]+@[^@]+\.[^@]+", username):
        print(f"{R}❌ Invalid email format.{W}")
        return

    # Check for duplicates
    for d in users.values():
        if d.get('username', '').lower() == username.lower() or d.get('email', '').lower() == username.lower():
            print(f"{R}❌ A user with this username or email already exists.{W}")
            return

    # Role selection
    print(f"\nAvailable Roles: {G}viewer{W} (read-only), {Y}officer{W} (standard), {R}admin{W} (full access)")
    role = input("Role [viewer/officer/admin]: ").strip().lower()
    if role not in ('viewer', 'officer', 'admin'):
        print(f"{Y}⚠️  Invalid role. Defaulting to 'viewer'.{W}")
        role = 'viewer'

    # Password input
    while True:
        pwd = getpass.getpass(f"Password (min {MIN_PASSWORD_LENGTH} chars): ")
        if len(pwd) < MIN_PASSWORD_LENGTH:
            print(f"{R}❌ Password too short. Minimum {MIN_PASSWORD_LENGTH} characters required.{W}")
            continue
            
        confirm = getpass.getpass("Confirm Password: ")
        if pwd != confirm:
            print(f"{R}❌ Passwords do not match. Try again.{W}")
            continue
        break

    # Create user
    uid = f"user_{str(len(users) + 1).zfill(3)}"
    hashed = bcrypt.hashpw(pwd.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    users[uid] = {
        'username': username,
        'password': hashed,
        'role': role,
        'email': username if '@' in username else '',
        'created_at': datetime.now().isoformat(),
        'failed_attempts': 0,
        'locked_until': None,
        'lockout_count': 0,
        'created_by': 'admin_cli'
    }
    
    save_users(users)
    print(f"\n{G}✅ User created successfully!{W}")
    print(f"   👤 Username: {username}")
    print(f"   🔑 Role:     {role}")
    print(f"   🌐 Login:    http://localhost:5000\n")

def cmd_delete():
    print(f"\n{BOLD}🗑️  Delete User{W}\n{'-'*40}")
    users = safe_load_users()
    cmd_list()
    
    uid = input("Enter User ID to delete (e.g., user_001): ").strip()
    if uid not in users:
        print(f"{R}❌ User ID not found.{W}")
        return
        
    # 🛡️ SAFETY CHECK: Prevent deleting the last admin
    if users[uid].get('role') == 'admin':
        active_admins = [u for u_id, u in users.items() if u.get('role') == 'admin' and not u.get('permanent_lock')]
        if len(active_admins) <= 1:
            print(f"{R}🚫 SECURITY BLOCK: Cannot delete the last active admin account.{W}")
            print(f"   Promote another user to admin first, or create a new admin.{W}\n")
            return

    name = users[uid].get('username', 'Unknown')
    confirm = input(f"Are you sure you want to permanently delete '{name}'? (yes/no): ").strip().lower()
    
    if confirm == 'yes':
        del users[uid]
        save_users(users)
        print(f"\n{G}✅ Successfully deleted user: {name}{W}\n")
    else:
        print(f"{Y}⚠️  Deletion cancelled.{W}\n")

def cmd_reset_password():
    print(f"\n{BOLD}🔑 Reset User Password{W}\n{'-'*40}")
    users = safe_load_users()
    cmd_list()
    
    uid = input("Enter User ID to reset: ").strip()
    if uid not in users:
        print(f"{R}❌ User ID not found.{W}")
        return
        
    while True:
        pwd = getpass.getpass(f"New Password (min {MIN_PASSWORD_LENGTH} chars): ")
        if len(pwd) < MIN_PASSWORD_LENGTH:
            print(f"{R}❌ Password too short. Minimum {MIN_PASSWORD_LENGTH} characters required.{W}")
            continue
            
        confirm = getpass.getpass("Confirm New Password: ")
        if pwd != confirm:
            print(f"{R}❌ Passwords do not match. Try again.{W}")
            continue
        break

    users[uid]['password'] = bcrypt.hashpw(pwd.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    # Also clear any lockouts when an admin resets the password
    users[uid]['failed_attempts'] = 0
    users[uid]['locked_until'] = None
    users[uid]['permanent_lock'] = False
    
    save_users(users)
    print(f"\n{G}✅ Password successfully reset for: {users[uid]['username']}{W}\n")

def cmd_unlock():
    print(f"\n{BOLD}🔓 Unlock User Account{W}\n{'-'*40}")
    users = safe_load_users()
    cmd_list()
    
    uid = input("Enter User ID to unlock: ").strip()
    if uid not in users:
        print(f"{R}❌ User ID not found.{W}")
        return
        
    users[uid]['failed_attempts'] = 0
    users[uid]['locked_until'] = None
    users[uid]['lockout_count'] = 0
    users[uid]['permanent_lock'] = False
    
    save_users(users)
    print(f"\n{G}✅ Account fully unlocked: {users[uid]['username']}{W}\n")

def cmd_promote():
    print(f"\n{BOLD}⬆️  Promote User to Admin{W}\n{'-'*40}")
    users = safe_load_users()
    cmd_list()
    
    uid = input("Enter User ID to promote: ").strip()
    if uid not in users:
        print(f"{R}❌ User ID not found.{W}")
        return
        
    old_role = users[uid].get('role', 'unknown')
    users[uid]['role'] = 'admin'
    save_users(users)
    
    print(f"\n{G}✅ Successfully promoted '{users[uid]['username']}' from '{old_role}' to 'admin'{W}\n")

# Command Registry
COMMANDS = {
    'list-users': cmd_list,
    'add-user': cmd_add,
    'delete-user': cmd_delete,
    'reset-password': cmd_reset_password,
    'unlock-user': cmd_unlock,
    'promote-admin': cmd_promote
}

def menu():
    print(f"{BOLD}Available Commands:{W}")
    for i, cmd in enumerate(COMMANDS, 1):
        print(f"  {G}{i}.{W} {cmd.replace('-', ' ').title()}")
    print(f"  {R}0.{W} Exit\n")
    
    choice = input("Choose an option (number or command name): ").strip()
    
    if choice == '0':
        print(f"\n{G}👋 Exiting Management Tool. Goodbye!{W}\n")
        sys.exit(0)
        
    try:
        idx = int(choice) - 1
        cmd = list(COMMANDS.keys())[idx]
        COMMANDS[cmd]()
    except (ValueError, IndexError):
        if choice in COMMANDS:
            COMMANDS[choice]()
        else:
            print(f"\n{R}❌ Unknown command. Please try again.{W}\n")

if __name__ == '__main__':
    banner()
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd in COMMANDS:
            COMMANDS[cmd]()
        else:
            print(f"{R}❌ Unknown command: '{cmd}'{W}")
            print(f"{Y}Available commands: {', '.join(COMMANDS.keys())}{W}\n")
    else:
        # Interactive menu mode
        while True:
            menu()