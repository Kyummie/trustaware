from functools import wraps
from flask import session, redirect, url_for, abort


def login_required(view):
    """Redirect to login if there's no active session."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login'))
        return view(*args, **kwargs)
    return wrapped


def role_required(*allowed_roles):
    """Require login AND that session['role'] is one of allowed_roles.

    Usage: @role_required('cisoaas')  or  @role_required('sme_admin', 'cisoaas')
    """
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('auth.login'))
            if session.get('role') not in allowed_roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator