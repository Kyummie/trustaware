from flask import Blueprint

employee_bp = Blueprint('employee', __name__)

@employee_bp.route('/dashboard')
def dashboard():
    return 'Employee dashboard coming soon'