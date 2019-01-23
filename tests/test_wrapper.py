from testapp import *

import pytest
import time

@pytest.fixture(scope="session",
        params=[('tws', 7496, 999), ])
def app(request):
    app = TestIBApp(*request.param)
    print("starting app")
    app.start()
    for tries in range(100):
        if app.isConnected():
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("Unable to connect to TWS at {}".format(request.param))

    yield app  # provide the fixture value
    print("teardown app")
    app.stop()


def test_realtime_bars(app):
    contract = Contract()
    contract.symbol = 'ES'
    contract.exchange = 'GLOBEX'
    contract.secType = "FUT"
    details = app.reqContractDetails(contract)

    instrument = details[0]

    # request realtime bars
    timeframe = 5  #  is ignored by TWSAPI by 2019-01-01
    N = 3
    lapse = (N - 1) * timeframe + 1

    answer = app.reqRealTimeBars(instrument.contract, timeframe, 'TRADES', 0, [])

    local_symbol = answer._call_args[1][0].localSymbol
    assert local_symbol == instrument.contract.localSymbol

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




