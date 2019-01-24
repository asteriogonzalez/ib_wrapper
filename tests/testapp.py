# -*- coding: utf-8 -*-

from ibapi.contract import Contract
from ibapi.common import BarData
from ib_wrapper.ib_wrapper import IBApp, EClient, EWrapper

class TestIBApp(IBApp):
    # def historicalData(self, reqId: int, bar: BarData):
        # print(bar)
        # if bar.date == '1548280740':
            # foo = 1  # last

    def position(self, account: str, contract: Contract, position: float,
                 avgCost: float):
        super().position(account, contract, position, avgCost)
        print("Position.", "Account:", account, "Symbol:", contract.symbol, "SecType:",
              contract.secType, "Currency:", contract.currency,
            "Position:", position, "Avg cost:", avgCost)

    def positionEnd(self):
        super().positionEnd()
        print("PositionEnd")
