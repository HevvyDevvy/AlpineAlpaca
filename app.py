import os
import threading

from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import (
    LoginManager, login_user, logout_user, login_required, current_user,
)

from models import db, User, ApiCredential, TradeLog, StrategyPerformance
import trading_bot
import market_view

# One background thread + stop_event per logged-in user, kept in memory.
# NOTE: this only works with a single app process/worker. Once this is a
# real hosted service running multiple gunicorn workers, move this state
# into something shared (e.g. a Celery task + Redis) instead of an
# in-process dict, or each worker will have its own view of "who is trading".
running_threads = {}
stop_events = {}


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY")
    if not app.config["SECRET_KEY"]:
        raise RuntimeError("SECRET_KEY environment variable is not set.")

    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL", "sqlite:///alpine_alpaca.db"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    login_manager = LoginManager()
    login_manager.login_view = "login"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    @app.after_request
    def add_security_headers(response):
        # All images are now bundled locally (static/images/) so the CSP no
        # longer needs to allow external image hosts.
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    with app.app_context():
        db.create_all()

    # ---------- auth ----------

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm", "")

            if not email or not password:
                flash("Email and password are required.")
            elif password != confirm:
                flash("Passwords do not match.")
            elif len(password) < 10:
                flash("Password must be at least 10 characters.")
            elif User.query.filter_by(email=email).first():
                flash("An account with that email already exists.")
            else:
                user = User(email=email)
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                login_user(user)
                return redirect(url_for("dashboard"))

        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = User.query.filter_by(email=email).first()
            if user and user.check_password(password):
                login_user(user)
                return redirect(url_for("dashboard"))
            flash("Invalid email or password.")
        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("login"))

    # ---------- dashboard ----------

    @app.route("/", methods=["GET", "POST"])
    @login_required
    def dashboard():
        user_id = current_user.id

        if request.method == "POST":
            if "save_keys" in request.form:
                api_key = request.form.get("api_key", "").strip()
                api_secret = request.form.get("api_secret", "").strip()
                is_live = request.form.get("use_live") == "on"

                if not api_key or not api_secret:
                    flash("API key and secret are required.")
                else:
                    cred = current_user.credential or ApiCredential(user_id=user_id)
                    cred.set_keys(api_key, api_secret)
                    cred.is_live = is_live
                    db.session.add(cred)
                    db.session.commit()
                    flash("API credentials saved (encrypted).")

            elif "start" in request.form:
                if user_id in running_threads and running_threads[user_id].is_alive():
                    flash("Trading is already running!")
                elif not current_user.credential:
                    flash("Save your API credentials first.")
                else:
                    try:
                        money_available = float(request.form.get("money", 0))
                    except ValueError:
                        money_available = 0

                    if money_available <= 0:
                        flash("Amount to invest must be a positive number.")
                    else:
                        api_key, api_secret = current_user.credential.get_keys()
                        base_url = current_user.credential.base_url
                        stop_event = threading.Event()
                        stop_events[user_id] = stop_event
                        t = threading.Thread(
                            target=trading_bot.run_cycle,
                            args=(app, user_id, api_key, api_secret, base_url, money_available, stop_event),
                            daemon=True,
                        )
                        running_threads[user_id] = t
                        t.start()
                        mode = "LIVE" if current_user.credential.is_live else "paper"
                        flash(f"Trading started in {mode} mode.")

            elif "stop" in request.form:
                t = running_threads.get(user_id)
                se = stop_events.get(user_id)
                if t and t.is_alive() and se:
                    se.set()
                    t.join(timeout=10)
                    flash("Stop requested — bot will finish its current step and halt.")
                else:
                    flash("No active trading session.")

        is_running = user_id in running_threads and running_threads[user_id].is_alive()
        perf = StrategyPerformance.query.filter_by(user_id=user_id).all()
        recent_trades = (
            TradeLog.query.filter_by(user_id=user_id)
            .order_by(TradeLog.opened_at.desc())
            .limit(20)
            .all()
        )

        return render_template(
            "dashboard.html",
            is_running=is_running,
            has_keys=current_user.credential is not None,
            is_live=current_user.credential.is_live if current_user.credential else False,
            performance=perf,
            trades=recent_trades,
        )

    @app.route("/market")
    @login_required
    def market():
        if not current_user.credential:
            flash("Save your API credentials first.")
            return redirect(url_for("dashboard"))

        api_key, api_secret = current_user.credential.get_keys()
        base_url = current_user.credential.base_url
        performance_by_strategy = {
            name: StrategyPerformance.query.filter_by(user_id=current_user.id, strategy_name=name).first()
            for name in ["ma_crossover", "rsi_reversion", "macd_momentum",
                         "bollinger_reversion", "zscore_mean_reversion",
                         "donchian_breakout", "vwap_trend"]
        }

        try:
            snapshot = market_view.build_snapshot(api_key, api_secret, base_url, performance_by_strategy)
        except Exception as e:
            flash(f"Couldn't load market data: {e}")
            snapshot = {"by_symbol": [], "by_strategy": {}}

        return render_template("market.html", snapshot=snapshot)

    return app


if __name__ == "__main__":
    app = create_app()
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug_mode, port=int(os.environ.get("PORT", 8080)))
