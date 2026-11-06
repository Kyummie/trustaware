from flask import Flask, redirect, url_for
from dotenv import load_dotenv
import os

load_dotenv()

def create_app():
    app = Flask(__name__)
    app.secret_key = os.getenv('SECRET_KEY')

    from app.routes.auth import auth_bp
    from app.routes.employee import employee_bp
    from app.routes.admin import admin_bp
    from app.routes.cisoaas import cisoaas_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(employee_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(cisoaas_bp)

    @app.route('/')
    def index():
        return redirect(url_for('auth.login'))

    return app