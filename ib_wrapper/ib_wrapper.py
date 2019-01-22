# -*- coding: utf-8 -*-

"""Dynamic TWS-IB API wrapping module to make callings blocking and
avoid user to deal with asyncrhonous world.
"""
import re
import inspect
from threading import Thread, Lock

import ibapi.wrapper
import ibapi.client
from ibapi.wrapper import EWrapper
from ibapi.client import EClient, decoder
from ibapi.contract import *

# def getfunc():
    # from inspect import currentframe, getframeinfo
    # caller = currentframe().f_back
    # func_name = getframeinfo(caller)[2]
    # caller = caller.f_back
    # func = caller.f_locals.get(
            # func_name, caller.f_globals.get(
                # func_name
        # )
    # )
    # return func


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
        self._call_args = None
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

    There are some function groups:

    - BLOCKING:     Blocking methods.
    - ONESHOT:      One shot methods.
    - SUBSCRIPTION: Subscription methods.
    - SUBMULTIPLE:  Subscription with multiple callbacks for receiving data.

    Wrapper would try to identify the type of API method, find the ones
    that works together and set the behavior for the wrapping function to
    act in correct manner.

    The rules to wrapping and grouping are based on function name and
    signature.
    """
    reqid = 0

    BEHAVIOR = {
        'BLOCKING': dict(timeout=60, one_shot=False, multiple_callbacks=False),
        'ONESHOT' : dict(timeout=60, one_shot=True, multiple_callbacks=False),
        'SUBSCRIPTION': dict(timeout=-1, one_shot=False, multiple_callbacks=False),
        'SUBMULTIPLE': dict(timeout=-1, one_shot=False, multiple_callbacks=True),
    }

    def __init__(self, excluded=('error', )):
        super(IBWrapper, self).__init__()
        self._req2data = dict()
        self._excluded_methods = excluded
        self.timeout = 60
        self._wrapper_context = dict()
        # self._diff_state = DiffState()

    def next_rid(self):
        "Generate a new sequential request ID"
        self.reqid += 1
        return self.reqid

    # def gen_key(self, *args, **kw):
        # """Generate a default key for each call"""
        # # TODO: convert kw to posicional args
        # return args

    def get_container(self, f):
        return self._req2data
        # context = self._wrapper_context[f]
        # key = context['key']
        # container = self._req2data.setdefault(key, dict())
        # return container

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
        def split_plurals(name):
            splitted = re.sub('(?!^)([A-Z][a-z]+)', r' \1', name).split()
            for i, key in enumerate(splitted):
                if key.endswith('s'):
                    key = '({}|{})'.format(key, key[:-1])
                    splitted[i] = key
            return ''.join(splitted)

        def get_signature(method):
            sig = inspect.signature(method)
            result = "{}({})".format(
                method.__name__,
                ','.join(sig.parameters.keys()))
            return result

        methods = inspect.getmembers(instance, inspect.ismethod)
        wrapped = []
        available = dict([(get_signature(method), method) for (_, method) in \
                          inspect.getmembers(instance, inspect.ismethod)])

        patterns = [

            # blocking req X, answer X, req X Ends
            # e.g. reqAccountSummary, accountSummary, accountSummaryEnd
            ('BLOCKING', [
                (r'req(?P<key>.*?)\((reqId|requestId),.*', self.wrap_call),
                (r'{fname}\((reqId|requestId),.*', self.wrap_receive),
                (r'{fname}End\((reqId|requestId)[,\)]', self.wrap_ends),
                ]
             ),

            # subcription req X, answer X, cancel X
            # e.g. reqAccountSummary, accountSummary, accountSummaryEnd
            ('SUBSCRIPTION', [
                (r'req(?P<key>.*?)\((reqId|requestId),.*', self.wrap_call),
                (r'{fname}\((reqId|requestId),.*', self.wrap_receive),
                (r'cancel{fname}\((reqId|requestId)[,\)]', self.wrap_ends),
                ]
            ),
            # # subcription with multiples callbacks for receiving data
            # # req X, answer_i X, cancel X
            # # e.g. reqMktDepth, updateMktDepth, updateMktDepthL2, cancelMktDepth
            # #
            # [
                # (r'req(?P<key>.*?)\((reqId|requestId),.*', self.non_blocking_call),
                # (r'update{fname}\((reqId|requestId),.*', self.wrap_receive),
                # (r'update{fname}L2?\((reqId|requestId),.*', self.wrap_receive),
                # (r'cancel{fname}\((reqId|requestId)[,\)]', self.wrap_ends),
            # ],


            # #  ignore private/protected methods
            # [
                # (r'(?P<key>(_+).*?(_.*))\(.*', None),
            # ],
        ]

        # TODO: retire matched signatures and not matched signatures as well



        # available = dict([(sig, value) for (sig, value) in available.items() if 'MktDepth'.lower() in sig.lower()])

        # TODO: remove this debugging code
        foo = dict()
        knowns = [
            '__init__',
            # 'accountSummary',
            # 'historicalNews',
            # 'reqAccountUpdatesMulti', 'accountUpdateMulti', 'accountUpdateMultiEnd',
            # 'contractDetails',
            # 'reqFundamentalData', 'fundamentalData', 'cancelFundamentalData',
            # 'reqHeadTimeStamp', 'headTimestamp', 'cancelHeadTimeStamp',
            # 'reqHistoricalData', 'historicalData', 'historicalDataEnd',
            # 'reqHistoricalTicks', 'xxx', 'xxxx',
            # 'reqMktDepth', 'updateMktDepth', 'updateMktDepthL2', 'cancelMktDepth',
            # 'reqPnL', 'pnl', 'cancelPnL',
            # 'reqPositionsMulti', 'positionsMulti', 'positionMultiEnd',
            # 'reqRealTimeBars', 'realtimeBar', 'cancelRealTimeBars',
            # 'xxx', 'xxx', 'xxxx',
            # 'xxx', 'xxx', 'xxxx',
            # 'xxx', 'xxx', 'xxxx',
        ]
        todo = [
            #  anoying
            'reqExecutions', 'execDetails', 'execDetailsEnd',
            'historicalTicks', 'historicalTicksBidAsk', 'historicalTicksLast',
            'reqMatchingSymbols', 'symbolSamples',
            'reqMktData', 'cancelMktData',
            'reqNewsArticle', 'newsArticle',
            'reqScannerSubscription',
            'reqSecDefOptParams',
            'reqSmartComponents', 'smartComponents'
            'xxxx',
            'xxxx',
            'xxxx',
            'xxxx',
            'xxxx',
            'xxxx',
            'xxxx',
        ]

        todo.extend(knowns)

        valid = ['reqRealTimeBars', 'realtimeBar', 'cancelRealTimeBars',
                  'reqcontractDetails', 'contractDetails', 'contractDetailsEnd']

        # for sig, method in available.items():
            # for k in todo:
                # if k.lower() in sig.lower():
                    # break
            # else:
                # foo[sig] = method
        # available = foo

        for sig, method in available.items():
            for k in valid:
                if k.lower() in sig.lower():
                    foo[sig] = method
                    break

        available = foo

        for sig in available:
            if 'reqMktData'.lower() in sig.lower():
                print(' {}'.format(sig))

        kk = 'reqRealTimeBars'
        while available:
            unique_matches = list()
            partial_failed_matched = list()
            # iterate all pattern groups, so only
            # 1 group must match at the same time
            candidates = dict(available)
            any_matched = 0
            for group, patgroup in patterns:
                context = dict(self.BEHAVIOR[group])
                matches = dict()
                print("........... candidates: {}".format(len(candidates)))
                for pat, wrap in patgroup:
                    exp = pat.format(**context)  # expand pattern
                    # seach for next desired method in group
                    for sig, method in candidates.items():
                        if kk.lower() in method.__name__.lower():
                            print('{} with {}'.format(sig, exp))
                            foo = 1
                        m = re.match(exp, sig, re.IGNORECASE)
                        if m:
                            d = m.groupdict()
                            context.update(d)
                            if 'key' in d:
                                context['fname'] = split_plurals(d['key'])
                            matches[sig] = (pat, method, wrap, context)
                            print('+ match {} with {}'.format(exp, sig))
                            any_matched += 1
                            if kk in method.__name__:
                                foo = 1
                            break  # go for the next method to match
                    else:
                        # not method has matched a group rule.
                        # go for next group as group will be discarded
                        if matches:
                            print('- partial filled group failed !! {}'.format(exp))
                            partial_failed_matched.append(matches)
                            matches = dict()
                        break
                    foo = 1
                if matches:
                    unique_matches.append(matches)
                    # restrict the search for next patterns to matched methods
                    candidates = dict([(sig, method) for (sig, (pat, method, wrap, context)) in matches.items()])
                    foo = 1

            l = len(unique_matches)
            if l == 1:
                # a single group has been matched, so we can wrap all
                # the matched methods
                matches = unique_matches[0]
                print("- Wrapped Group methods {}".format('-'*40))
                for sig, (pat, method, wrap, context) in matches.items():
                    if wrap:
                        print('wrapping: {} with {}'.format(sig, wrap.__name__))
                        setattr(instance, method.__name__, wrap(method, **context))
                    else:
                        print('ignoring {} by pattern {}'.format(sig, pat))
                    available.pop(sig)
                foo = 1
            elif l > 1:
                # more that 1 group has been matched, so patterns are poorly
                # defined, need more exclussion specification.
                print("ERROR: AMBIGUITY in PATTERN WRAP DEFINITIONS")
                for i, matches in enumerate(unique_matches):
                    print("- Group {}{}".format(i, '-'*40))
                    for sig, (pat, method, wrap) in matches.items():
                        print('- {}: {}'.format(sig, pat, ))
                        available.pop(sig, None)
                foo = 1  # TODO: raise an exception and stop?
            elif partial_failed_matched:
                print("ERROR: PARTIAL MATCHED but NONE FULL MATCHED")
                for i, matches in enumerate(partial_failed_matched):
                    print("- Group {}{}".format(i, '-'*40))
                    for sig, (pat, method, wrap, context) in matches.items():
                        print('- removing {}: {}'.format(sig, pat, ))
                        available.pop(sig, None)
                foo = 1  # TODO: raise an exception and stop?

            elif not any_matched:
                # the remain available methods can not be wrapped
                # print and continue
                print("WARNING: UNWRAPPED METHODS")
                for sig in available:
                    print (" ? {}".format(sig))

                break

        foo = 1

    def make_call(self, f, args, kw):
        "Prepare Answer placeholder and the key to analyze response history."
        reqid = self.next_rid()
        container = self.get_container(f)
        # key = self.gen_key(f, args, **kw)
        answer = container[reqid] = Answer()
        answer._call_args = (f, args, kw)
        f(reqid, *args, **kw)
        return container, reqid, answer

    def wrap_call(self, f, **context):
        """Get a new request Id, prepare an answer to hold all partial data
        and make the underlaying API call.
        """
        def wrap(*args, **kw):
            # lapse = kw.pop('polling', -1)  # -1 will stop future calls
            # self.reschedule(f, -1, lapse, args, kw)
            container, reqid, answer = self.make_call(f, args, kw)
            # handle blocking response until timeout
            context = self._wrapper_context[f]
            timeout = context['timeout']
            if timeout > 0:
                if not answer.acquire(timeout):
                    raise TimeoutError("waiting finishing {}".format(f))
                container.pop(reqid)
            return answer
        self._wrapper_context[f] = context
        return wrap

    def wrap_receive(self, f, **context):
        """Collect all the responses until request is completely finished."""
        def wrap(*args, **kw):
            container = self.get_container(f)
            reqid, args = args[0], args[1:]
            if len(args) == 1:
                container[reqid].append(*args)
            else:
                container[reqid].append(args)

            context = self._wrapper_context[f]
            if context['one_shot']:
                self._cancel_request(f, reqid)

            return f(reqid, *args, **kw)
        self._wrapper_context[f] = context
        return wrap

    def wrap_ends(self, f, **context):
        """Handle the end of a request
        - Release blocking thread that is waiting the response (if any).
        - Update differencial state for the key associated with the call.
        """
        def wrap(reqid):
            self._cancel_request(f, reqid)
            return f(reqid)
        self._wrapper_context[f] = context
        return wrap

    def _cancel_request(self, f, reqid):
        container = self.get_container(f)
        answer = container[reqid]
        answer.release()
        # key = answer.key
        # compute and process differences
        # self._diff_state.update(answer.values, key)






        # for fname, method in inspect.getmembers(instance, inspect.ismethod):
            # sig = get_signature(method)
            # print(sig)
            # foo = 1

        # for (fname, meth) in methods:
            # if fname in self._excluded_methods:
                # continue
            # sig = inspect.signature(meth)
            # if fname.endswith('End'):
                # print("- wrap end: {}".format(fname))
                # setattr(instance, fname, self.wrap_ends(meth))
                # continue
            # # print('{}: {}'.format(fname, sig.parameters))
            # for p in sig.parameters.keys():
                # if p == 'reqId':
                    # if fname.startswith('req'):
                        # print("> wrap request: {}".format(fname))
                        # setattr(instance, fname, self.wrap_call(meth, key=fname))
                    # else:
                        # print("< wrap response: {}".format(fname))
                        # setattr(instance, fname, self.wrap_receive(meth))
                # break  # only 1st parameter


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
