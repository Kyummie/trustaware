from flask import Blueprint, render_template, request, redirect, url_for, session

# Adjust this import to match wherever your Supabase client is initialised,
# e.g. app/supabase_client.py with: supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
from app.supabase_client import supabase

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')

    email = request.form.get('email', '').strip()
    password = request.form.get('password', '')

    if not email or not password:
        return render_template('login.html', error='Please enter both email and password.')

    # Authenticate against Supabase Auth
    try:
        auth_response = supabase.auth.sign_in_with_password({
            "email": email,
            "password": password
        })
    except Exception:
        return render_template('login.html', error='Invalid email or password.')

    user = auth_response.user
    supabase_session = auth_response.session

    if not user or not supabase_session:
        return render_template('login.html', error='Invalid email or password.')

    # Look up this user's profile for organisation_id + role (RBAC)
    profile_result = (
        supabase.table('profiles')
        .select('*')
        .eq('id', user.id)
        .single()
        .execute()
    )
    profile = profile_result.data

    if not profile:
        return render_template(
            'login.html',
            error='No profile found for this account. Contact your administrator.'
        )

    # Persist what we need in the Flask session
    session['user_id'] = user.id
    session['email'] = user.email
    session['role'] = profile['role']
    session['organisation_id'] = profile.get('organisation_id')
    session['access_token'] = supabase_session.access_token
    session['refresh_token'] = supabase_session.refresh_token

    # Redirect based on role
    if profile['role'] == 'employee':
        return redirect(url_for('employee.dashboard'))
    elif profile['role'] == 'sme_admin':
        return redirect(url_for('admin.admin_dashboard'))
    elif profile['role'] == 'cisoaas':
        return redirect(url_for('cisoaas.dashboard'))
    else:
        return render_template('login.html', error='Unrecognised role. Contact your administrator.')


@auth_bp.route('/logout')
def logout():
    try:
        supabase.auth.sign_out()
    except Exception:
        pass  # session may already be invalid/expired — clear local session regardless
    session.clear()
    return redirect(url_for('auth.login'))