# -*- coding: utf-8 -*-
"""
DONE:
- Check that connection is a demo connection, not real trading.
- Connection, Disconnection, Reconnect.
- Test Real Time Bars subscription.
- High resolution historical downloader.
- Capture errors related to a request and cancel it.

TODO:
- Place MKT orders
- Place Bracket orders
- Use trailing Stops
- Monitorize positions
- Monitorize past executions
- Market data subscriptions
- Download historical data and keep live data subscription
"""

from testapp import *

import pytest
import time
from operator import mul
from functools import reduce


@pytest.fixture(scope="session",
        params=[('tws', 7496, 999), ])
def app(request):
    app = TestIBApp(*request.param)
    print("starting app")
    app.start()
    # answer = app.reqManagedAccts()

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

    assert pytest.approx(e3, abs=1.0) == e1
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

    local_symbol = answer.call_args[1][1].localSymbol
    assert local_symbol == ES.localSymbol

    # wait to receive some bars
    for i in range(1, lapse):
        n0 = len(answer)
        print('{}: received: {} bars so far from {}'.format(time.asctime(), n0, local_symbol))
        time.sleep(1)
        if answer:
            bar = answer[-1]
            assert reduce(mul, bar[1:5]) > 0  # open * high * low * close > 0. Volume may be zero

    assert pytest.approx(N, abs=1) == n0, "Check if market is closed at this time"

    # cancel subscription and check that no more bars are received
    app.cancelRealTimeBars(answer)

    for i in range(1, lapse):
        n0 = len(answer)
        print('{}: received: {} bars so far from {}'.format(time.asctime(), n0, local_symbol))
        time.sleep(1)

    assert pytest.approx(N, abs=1) == n0, "Check if market is closed at this time"
    foo = 1

def test_historical_data(app, ES):
    """Create a real time bar subscription, check that bars are sent
    and cancel subscription checking that no more bars are received.

    *NOTE*: IB *slow down* softly your queries and refuse to send data back
    in case your request are very intensive. That's mean any combination of
    parameters that makes you do not receive a *Pacing Violation* are good
    enough as the download speed for small bars is limited to a query around
    10 secs on average.

    https://interactivebrokers.github.io/tws-api/historical_limitations.html
    """
    timeframes = {
        '1s': ('1440 S', '1 secs'),
        '5s': ('7200 S', '5 secs'),
        '1m': ('1 D', '1 min'),
        '5m': ('1 D', '5 mins'),
        '4h': ('1 D', '4 hours'),
        '1d': ('1 Y', '1 day'),
    }

    barsize = '1s'
    duration, timeframe = timeframes[barsize]
    end = ''
    show = 'TRADES'

    # this is a blocking call
    answer = app.reqHistoricalData(
        ES,
        end,
        duration,
        timeframe,
        show,
        useRTH=True,
        formatDate=1,  # 1: '20190109  15:30:00', 2: '1547044200'
        keepUpToDate=False,
        chartOptions=None
    )

    assert answer[0].date < answer[-1].date, \
           "historical data is not sorted as expected"

    expected = eval(duration.split()[0])
    assert pytest.approx(len(answer), abs=100) == expected, \
           "only {} bars received. Expected {}".format(len(answer), expected)

    print('Ok, received {} bars of {}'.format(expected, barsize))
    # app.cancelHistoricalData(answer) # this will fail even keepUpToDate is set

def test_place_orders(app):
    """
    - Get current position status

    - Place orders not to be filled

    - Move order until are filled
    https://interactivebrokers.github.io/tws-api/modifying_orders.html

    - Check position


    """
    pass





