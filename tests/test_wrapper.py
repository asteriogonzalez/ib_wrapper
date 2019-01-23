# -*- coding: utf-8 -*-
"""
DONE:
- Connection, Disconnection, Reconnect
- Test Real Time Bars subscription

TODO:
- Place MKT orders
- Place Bracket orders
- Use trailing Stops
- Monitorize positions
- Monitorize past executions
- Market data subscriptions
- High resolution historical downloader
"""

from testapp import *

import pytest
import time


@pytest.fixture(scope="session",
        params=[('tws', 7496, 999), ])
def app(request):
    app = TestIBApp(*request.param)
    print("starting app")
    app.start()

    yield app  # provide the fixture value
    print("teardown app")
    app.stop()

@pytest.fixture(scope="session")
def ES(app):
    "Return the current negociated ES contract. App must be running."
    contract = Contract()
    contract.symbol = 'ES'
    contract.exchange = 'GLOBEX'
    contract.secType = "FUT"
    details = app.reqContractDetails(contract)
    instrument = details[0]
    return instrument.contract

def test_reconnect(app):
    """Force socket failure to check autoreconnet mechanism.
    Check that callback has been fired and reconnection lapse has happend.
    """

    t0 = time.time()
    contract = ES(app)
    assert contract.conId, "unknown returned contract instance"
    t1 = time.time()

    app.conn.socket.close()

    contract = ES(app)
    assert contract.conId, "unknown returned contract instance"
    t2 = time.time()

    contract = ES(app)
    assert contract.conId, "unknown returned contract instance"
    t3 = time.time()

    e1 = t1 - t0
    e2 = t2 - t1
    e3 = t3 - t2

    pytest.approx(e3, rel=1.0) == e1
    assert e2 > e1  # > 2, "Reconnection lapse seems to not happend"


def test_realtime_bars(app, ES):
    """Create a real time bar subscription, check that bars are sent
    and cancel subscription checking that no more bars are received.
    """
    # request realtime bars
    timeframe = 5  #  is ignored by TWSAPI by 2019-01-01
    N = 3
    lapse = (N - 1) * timeframe + 1

    answer = app.reqRealTimeBars(ES, timeframe, 'TRADES', 0, [])

    local_symbol = answer._call_args[1][0].localSymbol
    assert local_symbol == ES.localSymbol

    # wait to receive some bars
    for i in range(1, lapse):
        n0 = len(answer)
        print('{}: received: {} bars so far from {}'.format(time.asctime(), n0, local_symbol))
        time.sleep(1)


    pytest.approx(N, abs=1) == n0

    # cancel subscription and check that no more bars are received
    app.cancelRealTimeBars(answer)

    for i in range(1, lapse):
        n0 = len(answer)
        print('{}: received: {} bars so far from {}'.format(time.asctime(), n0, local_symbol))
        time.sleep(1)

    pytest.approx(N, abs=1) == n0




