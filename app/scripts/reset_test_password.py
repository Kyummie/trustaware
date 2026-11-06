"""
Reset a TrustAware test user's password.

Purpose: test accounts use made-up email domains (e.g. abc.com), so password
reset emails never arrive. This sets a known password and confirms the email
so you can log in. For development with synthetic data only.
It uses the service-role key, so never expose it as a web route.

Usage (from the project folder, with .venv active):
    python scripts/reset_test_password.py t.bazil@abc.com 'NewPass123!'
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / '.env')

from app.supabase_client import supabase_admin


def main():
    if len(sys.argv) != 3:
        print('Usage: python scripts/reset_test_password.py <email> <new_password>')
        sys.exit(1)

    email, password = sys.argv[1], sys.argv[2]
    users = {u.email: u for u in supabase_admin.auth.admin.list_users()}
    if email not in users:
        print(f'No user found with email {email}')
        sys.exit(1)

    supabase_admin.auth.admin.update_user_by_id(
        users[email].id, {'password': password, 'email_confirm': True})
    print(f'Password reset and email confirmed for {email}')


if __name__ == '__main__':
    main()


"""
To use it, run this from the project folder with (.venv) active:

python scripts/reset_test_password.py p.iskandar@abc.com 'NewPass123!' 

"""