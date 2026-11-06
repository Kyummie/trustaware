import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()  # reads .env into environment variables

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")  # publishable key — respects RLS
SUPABASE_SECRET_KEY = os.environ.get("SUPABASE_SECRET_KEY")  # secret/service-role key — bypasses RLS

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in your .env file")

# Normal client — used for anything done "as the logged-in user"
# (login, reading your own profile, etc). Respects Row Level Security.
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Admin client — ONLY for privileged server-side operations that must
# bypass RLS, e.g. CISOaaS creating an org + SME admin account on
# someone's behalf. Never expose this client or its key to anything
# user-facing. None if SUPABASE_SECRET_KEY isn't set.
supabase_admin: Client | None = (
    create_client(SUPABASE_URL, SUPABASE_SECRET_KEY) if SUPABASE_SECRET_KEY else None
)