"""Tests for PriceUpdate dataclass."""

import pytest

from app.market.models import PriceUpdate


class TestPriceUpdate:
    """Unit tests for the PriceUpdate model."""

    def test_price_update_creation(self):
        """Test basic PriceUpdate creation."""
        update = PriceUpdate(ticker="AAPL", price=190.50, previous_price=190.00, timestamp=1234567890.0)
        assert update.ticker == "AAPL"
        assert update.price == 190.50
        assert update.previous_price == 190.00
        assert update.timestamp == 1234567890.0

    def test_change_calculation(self):
        """Test price change calculation."""
        update = PriceUpdate(ticker="AAPL", price=190.50, previous_price=190.00, timestamp=1234567890.0)
        assert update.change == 0.50

    def test_change_negative(self):
        """Test negative price change."""
        update = PriceUpdate(ticker="AAPL", price=189.50, previous_price=190.00, timestamp=1234567890.0)
        assert update.change == -0.50

    def test_change_percent_up(self):
        """Test percentage change calculation (up)."""
        update = PriceUpdate(ticker="AAPL", price=190.00, previous_price=100.00, timestamp=1234567890.0)
        assert update.change_percent == 90.0

    def test_change_percent_down(self):
        """Test percentage change calculation (down)."""
        update = PriceUpdate(ticker="AAPL", price=100.00, previous_price=200.00, timestamp=1234567890.0)
        assert update.change_percent == -50.0

    def test_change_percent_zero_previous(self):
        """Test percentage change with zero previous price."""
        update = PriceUpdate(ticker="AAPL", price=100.00, previous_price=0.00, timestamp=1234567890.0)
        assert update.change_percent == 0.0

    def test_direction_up(self):
        """Test direction calculation (up)."""
        update = PriceUpdate(ticker="AAPL", price=191.00, previous_price=190.00, timestamp=1234567890.0)
        assert update.direction == "up"

    def test_direction_down(self):
        """Test direction calculation (down)."""
        update = PriceUpdate(ticker="AAPL", price=189.00, previous_price=190.00, timestamp=1234567890.0)
        assert update.direction == "down"

    def test_direction_flat(self):
        """Test direction calculation (flat)."""
        update = PriceUpdate(ticker="AAPL", price=190.00, previous_price=190.00, timestamp=1234567890.0)
        assert update.direction == "flat"

    def test_to_dict(self):
        """Test serialization to dictionary."""
        update = PriceUpdate(ticker="AAPL", price=190.50, previous_price=190.00, timestamp=1234567890.0)
        result = update.to_dict()

        assert result["ticker"] == "AAPL"
        assert result["price"] == 190.50
        assert result["previous_price"] == 190.00
        assert result["timestamp"] == 1234567890.0
        assert result["change"] == 0.50
        assert result["change_percent"] == 0.2632  # (0.50 / 190.00) * 100
        assert result["direction"] == "up"

    def test_immutability(self):
        """Test that PriceUpdate is immutable."""
        update = PriceUpdate(ticker="AAPL", price=190.50, previous_price=190.00, timestamp=1234567890.0)

        with pytest.raises(AttributeError):
            update.price = 200.00  # Should raise error


class TestSessionOpen:
    """session_open / daily_change_percent behaviour."""

    def test_daily_change_percent(self):
        update = PriceUpdate(
            ticker="AAPL", price=190.52, previous_price=190.48, timestamp=1.0, session_open=190.00
        )
        assert update.daily_change_percent == pytest.approx(0.2737, abs=1e-4)

    def test_daily_change_negative(self):
        update = PriceUpdate("AAPL", 95.0, 96.0, 1.0, session_open=100.0)
        assert update.daily_change_percent == -5.0

    def test_defaults_to_zero_without_session_open(self):
        update = PriceUpdate("AAPL", 190.0, 189.0, 1.0)
        assert update.daily_change_percent == 0.0

    def test_zero_session_open_is_safe(self):
        update = PriceUpdate("AAPL", 1.0, 1.0, 1.0, session_open=0.0)
        assert update.daily_change_percent == 0.0

    def test_to_dict_includes_session_fields(self):
        update = PriceUpdate("AAPL", 110.0, 109.0, 1.0, session_open=100.0)
        result = update.to_dict()
        assert result["session_open"] == 100.0
        assert result["daily_change_percent"] == 10.0

    def test_to_dict_session_open_falls_back_to_price(self):
        result = PriceUpdate("AAPL", 110.0, 109.0, 1.0).to_dict()
        assert result["session_open"] == 110.0
