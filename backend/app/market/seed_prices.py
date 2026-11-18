"""Seed prices and per-ticker parameters for the market simulator.

The simulator supports a fixed universe of 50 well-known US tickers. Anything
outside this universe is rejected rather than given a made-up price.
"""

# Realistic starting prices (approximate)
SEED_PRICES: dict[str, float] = {
    # Default watchlist (10)
    "AAPL": 190.00,
    "GOOGL": 175.00,
    "MSFT": 420.00,
    "AMZN": 185.00,
    "TSLA": 250.00,
    "NVDA": 800.00,
    "META": 500.00,
    "JPM": 195.00,
    "V": 280.00,
    "NFLX": 600.00,
    # Tech (12)
    "AMD": 160.00,
    "INTC": 31.00,
    "ORCL": 125.00,
    "CRM": 270.00,
    "ADBE": 480.00,
    "CSCO": 48.00,
    "AVGO": 1350.00,
    "QCOM": 170.00,
    "IBM": 190.00,
    "UBER": 70.00,
    "SHOP": 70.00,
    "PLTR": 24.00,
    # Finance (8)
    "BAC": 38.00,
    "WFC": 58.00,
    "GS": 450.00,
    "MS": 95.00,
    "C": 62.00,
    "MA": 460.00,
    "PYPL": 63.00,
    "AXP": 235.00,
    # Consumer (10)
    "WMT": 60.00,
    "COST": 780.00,
    "HD": 345.00,
    "MCD": 270.00,
    "NKE": 95.00,
    "SBUX": 90.00,
    "KO": 62.00,
    "PEP": 170.00,
    "DIS": 105.00,
    "PG": 160.00,
    # Healthcare (5)
    "JNJ": 155.00,
    "UNH": 520.00,
    "PFE": 28.00,
    "LLY": 780.00,
    "MRK": 125.00,
    # Energy & industrials (5)
    "XOM": 115.00,
    "CVX": 155.00,
    "BA": 185.00,
    "CAT": 340.00,
    "GE": 160.00,
}

# The fixed set of tickers the simulator can produce prices for
SUPPORTED_TICKERS: frozenset[str] = frozenset(SEED_PRICES)

# Per-ticker GBM parameters
# sigma: annualized volatility (higher = more price movement)
# mu: annualized drift / expected return
TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL": {"sigma": 0.22, "mu": 0.05},
    "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT": {"sigma": 0.20, "mu": 0.05},
    "AMZN": {"sigma": 0.28, "mu": 0.05},
    "TSLA": {"sigma": 0.50, "mu": 0.03},  # High volatility
    "NVDA": {"sigma": 0.40, "mu": 0.08},  # High volatility, strong drift
    "META": {"sigma": 0.30, "mu": 0.05},
    "JPM": {"sigma": 0.18, "mu": 0.04},  # Low volatility (bank)
    "V": {"sigma": 0.17, "mu": 0.04},  # Low volatility (payments)
    "NFLX": {"sigma": 0.35, "mu": 0.05},
    # Tech
    "AMD": {"sigma": 0.45, "mu": 0.06},
    "INTC": {"sigma": 0.35, "mu": 0.03},
    "ORCL": {"sigma": 0.24, "mu": 0.05},
    "CRM": {"sigma": 0.30, "mu": 0.05},
    "ADBE": {"sigma": 0.30, "mu": 0.05},
    "CSCO": {"sigma": 0.19, "mu": 0.04},
    "AVGO": {"sigma": 0.35, "mu": 0.07},
    "QCOM": {"sigma": 0.32, "mu": 0.05},
    "IBM": {"sigma": 0.20, "mu": 0.04},
    "UBER": {"sigma": 0.38, "mu": 0.05},
    "SHOP": {"sigma": 0.48, "mu": 0.05},
    "PLTR": {"sigma": 0.55, "mu": 0.06},
    # Finance
    "BAC": {"sigma": 0.24, "mu": 0.04},
    "WFC": {"sigma": 0.24, "mu": 0.04},
    "GS": {"sigma": 0.25, "mu": 0.05},
    "MS": {"sigma": 0.25, "mu": 0.05},
    "C": {"sigma": 0.27, "mu": 0.04},
    "MA": {"sigma": 0.20, "mu": 0.05},
    "PYPL": {"sigma": 0.35, "mu": 0.03},
    "AXP": {"sigma": 0.24, "mu": 0.05},
    # Consumer
    "WMT": {"sigma": 0.17, "mu": 0.04},
    "COST": {"sigma": 0.20, "mu": 0.05},
    "HD": {"sigma": 0.21, "mu": 0.04},
    "MCD": {"sigma": 0.16, "mu": 0.04},
    "NKE": {"sigma": 0.28, "mu": 0.04},
    "SBUX": {"sigma": 0.27, "mu": 0.04},
    "KO": {"sigma": 0.14, "mu": 0.03},
    "PEP": {"sigma": 0.15, "mu": 0.03},
    "DIS": {"sigma": 0.28, "mu": 0.04},
    "PG": {"sigma": 0.14, "mu": 0.03},
    # Healthcare
    "JNJ": {"sigma": 0.15, "mu": 0.03},
    "UNH": {"sigma": 0.22, "mu": 0.05},
    "PFE": {"sigma": 0.22, "mu": 0.02},
    "LLY": {"sigma": 0.30, "mu": 0.08},
    "MRK": {"sigma": 0.19, "mu": 0.04},
    # Energy & industrials
    "XOM": {"sigma": 0.22, "mu": 0.04},
    "CVX": {"sigma": 0.22, "mu": 0.04},
    "BA": {"sigma": 0.35, "mu": 0.03},
    "CAT": {"sigma": 0.25, "mu": 0.05},
    "GE": {"sigma": 0.27, "mu": 0.05},
}

# Fallback parameters (only used defensively; unsupported tickers are rejected)
DEFAULT_PARAMS: dict[str, float] = {"sigma": 0.25, "mu": 0.05}

# Correlation groups for the simulator's Cholesky decomposition
# Tickers in the same group have higher intra-group correlation
CORRELATION_GROUPS: dict[str, set[str]] = {
    "tech": {
        "AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX",
        "AMD", "INTC", "ORCL", "CRM", "ADBE", "CSCO", "AVGO", "QCOM",
        "IBM", "UBER", "SHOP", "PLTR",
    },
    "finance": {"JPM", "V", "BAC", "WFC", "GS", "MS", "C", "MA", "PYPL", "AXP"},
    "consumer": {"WMT", "COST", "HD", "MCD", "NKE", "SBUX", "KO", "PEP", "DIS", "PG"},
    "healthcare": {"JNJ", "UNH", "PFE", "LLY", "MRK"},
    "energy_industrial": {"XOM", "CVX", "BA", "CAT", "GE"},
}

# Correlation coefficients
INTRA_TECH_CORR = 0.6  # Tech stocks move together
INTRA_FINANCE_CORR = 0.5  # Finance stocks move together
INTRA_OTHER_CORR = 0.5  # Consumer / healthcare / energy & industrial
CROSS_GROUP_CORR = 0.3  # Between sectors
TSLA_CORR = 0.3  # TSLA does its own thing
