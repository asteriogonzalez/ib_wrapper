# -*- coding: utf-8 -*-

from ib_wrapper.ib_wrapper import IBApp, EClient, EWrapper, Contract

class TestIBApp(IBApp):
    def position(self, account: str, contract: Contract, position: float,
                 avgCost: float):
        super().position(account, contract, position, avgCost)
        print("Position.", "Account:", account, "Symbol:", contract.symbol, "SecType:",
              contract.secType, "Currency:", contract.currency,
            "Position:", position, "Avg cost:", avgCost)

    def positionEnd(self):
        super().positionEnd()
        print("PositionEnd")
