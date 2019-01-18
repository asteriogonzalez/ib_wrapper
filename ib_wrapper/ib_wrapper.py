# -*- coding: utf-8 -*-

"""Main module."""
import inspect
from threading import Thread, Lock

import ibapi.wrapper
import ibapi.client
from ibapi.wrapper import EWrapper
from ibapi.client import EClient, decoder
from ibapi.contract import *


class Answer(list):
    """Safe storage of partial answers from server.
    When client try to acquire for reading, it will be locked until full
    response form client has been received.

    The answer is automatically locked at instantation by the current, so
    the only way that a client could consume the data is by releasing by
    the server side when answer is full filled.

    This is intended for Answers to be created in the network mainloop that
    will be process the partial responses from server.
    """
    def __init__(self, *args, **kw):
        self._lock = Lock()
        super(Answer, self).__init__(*args, **kw)
        self.acquire()

    def acquire(self, timeout=-1):
        "Try to lock the answer to fill it up or use it."
        return self._lock.acquire(timeout=timeout)

    def release(self):
        "Release the lock, usually done by server thread."
        self._lock.release()

    @property
    def values(self):
        "Convert the values to a ordinary list"
        return list(self)


class IBWrapper():
    """This wrapper use decorators to 'wrap' TWS API calls, from EWrapper and
    EClient as well in order to have blocking API calls, avoiding deal with
    asynchronous programming, new request id generation and so forth.

    The wrapping happens in dynamically so it will not required code
    maintenance when IB would release a new versions of the underlaying API.

    Due of TWS API design, the wrapping can be only made after connection, as
    the decoder instance that will be wrapped is only created on sucessful
    connection.

    The rule to wrapping is based on function name and signature.
    """
    reqid = 0

    def __init__(self, excluded=('error', )):
        super(IBWrapper, self).__init__()
        self._req2data = dict()
        self._excluded_methods = excluded
        self.timeout = 60
        # self._diff_state = DiffState()

    def next_rid(self):
        "Generate a new sequential request ID"
        self.reqid += 1
        return self.reqid

    # def gen_key(self, *args, **kw):
        # """Generate a default key for each call"""
        # # TODO: convert kw to posicional args
        # return args

    def make_call(self, f, args, kw):
        "Prepare Answer placeholder and the key to analyze response history."
        reqid = self.next_rid()
        # key = self.gen_key(f, args, **kw)
        answer = self._req2data[reqid] = Answer()
        f(reqid, *args, **kw)
        return reqid, answer

    def callwrap(self, f):
        """Get a new request Id, prepare an answer to hold all partial data
        and make the underlaying API call.
        """
        def wrap(*args, **kw):
            # lapse = kw.pop('polling', -1)  # -1 will stop future calls
            # self.reschedule(f, -1, lapse, args, kw)
            reqid, answer = self.make_call(f, args, kw)

            # handle blocking response until timeout
            if not answer.acquire(self.timeout):
                raise TimeoutError("waiting finishing {}".format(f))

            self._req2data.pop(reqid)
            return answer
        wrap.__wrapped__ = True
        return wrap

    def anserwrap(self, f):
        """Collect all the responses until request is completely finished."""
        def wrap(*args, **kw):
            reqid, args = args[0], args[1:]
            if len(args) == 1:
                self._req2data[reqid].append(*args)
            else:
                self._req2data[reqid].append(args)

            # print("answer for reqId {}: {}".format(reqid, args))
            return f(reqid, *args, **kw)
        return wrap

    def answerend(self, f):
        """Handle the end of a request
        - Release blocking thread that is waiting the response (if any).
        - Update differencial state for the key associated with the call.
        """
        def wrap(reqid):
            # print("end for reqId {}".format(reqid))
            answer = self._req2data[reqid]
            answer.release()
            # key = answer.key
            # compute and process differences
            # self._diff_state.update(answer.values, key)
            return f(reqid)
        return wrap

    def dinamic_wrapping(self, instance):
        """Iterate over EWrapper instance and EClient methods hold by decoder
        instance after connection.

        - If method has 'reqId' as first argument then function is a request
          or partial response.

          - Is method name starts with 'reqXXXXX' then is direct request call
            that we will be converted to a synchronous version.
          - Else is a partial response callback

        - If method endswith 'xxxxEnd' that means that request has finalize and
          data is ready for comsumption.

        It is safe to call more than once to this method.
        """
        methods = inspect.getmembers(instance, inspect.ismethod)
        for (fname, meth) in methods:
            if fname in self._excluded_methods:
                continue
            sig = inspect.signature(meth)
            if fname.endswith('End'):
                print("- wrap end: {}".format(fname))
                setattr(instance, fname, self.answerend(meth))
                continue
            # print('{}: {}'.format(fname, sig.parameters))
            for p in sig.parameters.keys():
                if p == 'reqId':
                    if fname.startswith('req'):
                        print("> wrap request: {}".format(fname))
                        setattr(instance, fname, self.callwrap(meth))
                    else:
                        print("< wrap response: {}".format(fname))
                        setattr(instance, fname, self.anserwrap(meth))
                break  # only 1st parameter


class IBApp(EWrapper, EClient):
    """The base class for any IB application.
    It combines a running network client and a wrapper for receiving callbacks.
    """

    def __init__(self, host='tws', port=7496, clientId=0):
        EWrapper.__init__(self)
        EClient.__init__(self, wrapper=self)

        self.dwrapper = IBWrapper()
        self._thread = Thread(target=self.run)
        self.host, self.port, self.clientId = host, port, clientId

    def start(self):
        "Connect and make the wrap, and start network main loop."
        self.reconnect()
        # need to be done after connection
        self.dwrapper.dinamic_wrapping(self)
        self._thread.start()

    def stop(self):
        "Stop the network client"
        self.done = True

    def reconnect(self):
        "Try to reconnect if is disconnected."
        if not self.isConnected():
            self.connect(self.host, self.port, self.clientId)


def test_contract_details(app):
    """Create some future contracts to get the current available contracts
    for this instrument.
    """
    ES = Contract()
    ES.secType = "FUT"
    ES.symbol = "ES"
    ES.exchange = "GLOBEX"
    for c in app.reqContractDetails(ES):
        print(c)
    print('-'*40)

    GE = Contract()
    GE.secType = "FUT"
    GE.symbol = "GE"
    GE.exchange = "GLOBEX"
    for c in app.reqContractDetails(GE):
        print(c)
    print('-'*40)

    # foo = app.reqContractDetails(GE, polling=5)
    # time.sleep(111)
    # app.reqContractDetails(GE, polling=-1)
    # time.sleep(111)


if __name__ == '__main__':

    app = IBApp()
    app.start()

    test_contract_details(app)

    app.stop()
