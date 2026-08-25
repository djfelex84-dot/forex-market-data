import os


TWELVE_DATA_API_KEY = os.getenv(
    "TWELVE_DATA_API_KEY"
)


SYMBOL = "EUR/USD"
INTERVAL = "5min"


EMA_FAST = 20
EMA_SLOW = 50

RSI_PERIOD = 14
ATR_PERIOD = 14


CANDLE_LIMIT = 120
CHECK_INTERVAL_SECONDS = 300


# =========================
# STRATEGY FILTERS
# =========================

MIN_EMA_DISTANCE_ATR = 0.15
MIN_ATR = 0.00010


RSI_BUY_MIN = 52
RSI_BUY_MAX = 68

RSI_SELL_MIN = 32
RSI_SELL_MAX = 48


# =========================
# SIGNAL EVALUATION
# =========================

OUTCOME_HORIZONS_MINUTES = (
    15,
    30,
    60,
)


# EUR/USD
# 1 pip = 0.0001
PIP_SIZE = 0.0001


# =========================
# VIRTUAL TRADE MODEL V2
# =========================

TRADE_MODEL_VERSION = "V2"

# SL based on volatility
STOP_LOSS_ATR_MULTIPLIER = 1.0

# But never smaller than this.
MIN_STOP_PIPS = 5.0

# TP = risk * 1.5
TAKE_PROFIT_R_MULTIPLE = 1.5

# Temporary test assumption.
# Later replaced with broker data.
ASSUMED_SPREAD_PIPS = 1.0

# Maximum trade duration.
MAX_TRADE_MINUTES = 180
