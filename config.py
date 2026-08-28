import os


TWELVE_DATA_API_KEY = os.getenv(
    "TWELVE_DATA_API_KEY"
)


# =========================
# MARKETS
# =========================

INSTRUMENTS = {
    "EUR/USD": {
        "market": "FOREX",
        "pip_size": 0.0001,
        "min_atr": 0.00010,
        "min_stop_pips": 5.0,
        "assumed_spread_pips": 1.0,
    },

    "GBP/USD": {
        "market": "FOREX",
        "pip_size": 0.0001,
        "min_atr": 0.00010,
        "min_stop_pips": 5.0,
        "assumed_spread_pips": 1.0,
    },
}


SYMBOLS = tuple(
    INSTRUMENTS.keys()
)


# Temporary compatibility value.
# Existing modules will be converted
# one by one to multi-market mode.
SYMBOL = SYMBOLS[0]


INTERVAL = "5min"


def get_instrument_config(symbol):
    if symbol not in INSTRUMENTS:
        raise ValueError(
            f"Unknown instrument: {symbol}"
        )

    return INSTRUMENTS[symbol]


# =========================
# INDICATORS
# =========================

EMA_FAST = 20
EMA_SLOW = 50

RSI_PERIOD = 14
ATR_PERIOD = 14


CANDLE_LIMIT = 1200
CHECK_INTERVAL_SECONDS = 300


# =========================
# STRATEGY FILTERS
# =========================

MIN_EMA_DISTANCE_ATR = 0.15


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


# =========================
# VIRTUAL TRADE MODEL V2
# =========================

TRADE_MODEL_VERSION = "V2"

STOP_LOSS_ATR_MULTIPLIER = 1.0

TAKE_PROFIT_R_MULTIPLE = 1.5

MAX_TRADE_MINUTES = 180


# =========================
# LEGACY COMPATIBILITY
# =========================
#
# These values keep the current
# EUR/USD-only modules working
# while we convert every file.
#
# New multi-market code will use
# get_instrument_config(symbol).

PIP_SIZE = (
    INSTRUMENTS[SYMBOL][
        "pip_size"
    ]
)

MIN_ATR = (
    INSTRUMENTS[SYMBOL][
        "min_atr"
    ]
)

MIN_STOP_PIPS = (
    INSTRUMENTS[SYMBOL][
        "min_stop_pips"
    ]
)

ASSUMED_SPREAD_PIPS = (
    INSTRUMENTS[SYMBOL][
        "assumed_spread_pips"
    ]
)
