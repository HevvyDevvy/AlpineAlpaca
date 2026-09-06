from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

import crypto_utils

db = SQLAlchemy()


def now():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=now)

    credential = db.relationship("ApiCredential", uselist=False, back_populates="user", cascade="all, delete-orphan")
    trades = db.relationship("TradeLog", back_populates="user", cascade="all, delete-orphan")
    strategy_stats = db.relationship("StrategyPerformance", back_populates="user", cascade="all, delete-orphan")

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)


class ApiCredential(db.Model):
    """One Alpaca connection per user. Keys are encrypted at rest and only
    decrypted transiently in memory when a trade cycle actually runs.
    """
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, unique=True)
    encrypted_api_key = db.Column(db.String(512), nullable=False)
    encrypted_api_secret = db.Column(db.String(512), nullable=False)
    is_live = db.Column(db.Boolean, default=False)  # False = paper trading
    updated_at = db.Column(db.DateTime, default=now, onupdate=now)

    user = db.relationship("User", back_populates="credential")

    def set_keys(self, api_key, api_secret):
        self.encrypted_api_key = crypto_utils.encrypt(api_key)
        self.encrypted_api_secret = crypto_utils.encrypt(api_secret)

    def get_keys(self):
        return (
            crypto_utils.decrypt(self.encrypted_api_key),
            crypto_utils.decrypt(self.encrypted_api_secret),
        )

    @property
    def base_url(self):
        return "https://api.alpaca.markets" if self.is_live else "https://paper-api.alpaca.markets"


class StrategyPerformance(db.Model):
    """Running track record per user per strategy, used by the adaptive
    allocator to decide how much capital each strategy earns going forward.
    """
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    strategy_name = db.Column(db.String(64), nullable=False)

    wins = db.Column(db.Integer, default=0)
    losses = db.Column(db.Integer, default=0)
    total_pnl = db.Column(db.Float, default=0.0)  # cumulative $ profit/loss attributed to this strategy
    trades_count = db.Column(db.Integer, default=0)
    updated_at = db.Column(db.DateTime, default=now, onupdate=now)

    user = db.relationship("User", back_populates="strategy_stats")

    __table_args__ = (db.UniqueConstraint("user_id", "strategy_name", name="uq_user_strategy"),)

    def record_trade(self, pnl):
        self.trades_count += 1
        self.total_pnl += pnl
        if pnl > 0:
            self.wins += 1
        else:
            self.losses += 1

    @property
    def win_rate(self):
        return self.wins / self.trades_count if self.trades_count else 0.0

    @property
    def avg_pnl(self):
        return self.total_pnl / self.trades_count if self.trades_count else 0.0


class TradeLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    symbol = db.Column(db.String(16), nullable=False)
    strategy_name = db.Column(db.String(64), nullable=False)
    side = db.Column(db.String(4), nullable=False)  # buy / sell
    qty = db.Column(db.Float, nullable=True)
    notional = db.Column(db.Float, nullable=True)
    price = db.Column(db.Float, nullable=True)
    pnl = db.Column(db.Float, nullable=True)  # filled in when the position is closed
    opened_at = db.Column(db.DateTime, default=now)
    closed_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User", back_populates="trades")
