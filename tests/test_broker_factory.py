"""Tests for the broker abstraction layer (Phase 1 multi-broker support)."""
import pytest
from broker.base import BaseBroker
from broker.factory import create_broker
from broker.alpaca_client import AlpacaClient


class TestBaseBroker:
    def test_cannot_instantiate_abstract_base(self):
        with pytest.raises(TypeError):
            BaseBroker()

    def test_alpaca_conforms_to_interface(self):
        client = create_broker("alpaca")
        assert isinstance(client, BaseBroker)
        assert isinstance(client, AlpacaClient)

    def test_is_market_open_default_delegates_to_clock(self):
        class DummyBroker(BaseBroker):
            def connect(self): pass
            def verify_connection(self): return True
            def get_account(self): return {}
            def get_clock(self): return {"is_open": True}
        assert DummyBroker().is_market_open() is True


class TestFactory:
    def test_default_provider_is_alpaca(self):
        assert isinstance(create_broker(), AlpacaClient)

    def test_explicit_alpaca(self):
        assert isinstance(create_broker("alpaca"), AlpacaClient)

    def test_provider_name_is_case_insensitive(self):
        assert isinstance(create_broker("ALPACA"), AlpacaClient)

    def test_unknown_provider_raises_value_error(self):
        with pytest.raises(ValueError):
            create_broker("robinhood")

    def test_ibkr_not_implemented_yet(self):
        with pytest.raises(NotImplementedError):
            create_broker("ibkr")

    def test_ibkr_aliases_not_implemented(self):
        for alias in ("ib", "interactive_brokers"):
            with pytest.raises(NotImplementedError):
                create_broker(alias)

    def test_kwargs_forwarded_to_client(self):
        client = create_broker("alpaca", api_key="KEY", secret_key="SECRET")
        assert client.api_key == "KEY"
        assert client.secret_key == "SECRET"
