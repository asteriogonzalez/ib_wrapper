# -*- coding: utf-8 -*-

"""Dynamic TWS-IB API wrapping module to make callings blocking and
avoid user to deal with asyncrhonous world.
"""
# TODO: convert realtimeBar to ibapi.common.BarData

import re
import time
import collections
from functools import reduce
from operator import and_
import inspect
from threading import Thread, Lock

import ibapi.wrapper
import ibapi.client
from ibapi.wrapper import EWrapper
from ibapi.client import EClient, decoder
from ibapi.contract import *

def flatten(l):
    #  from https://stackoverflow.com/questions/2158395/flatten-an-irregular-list-of-lists
    for el in l:
        if isinstance(el, collections.Iterable) and not isinstance(el, (str, bytes)):
            yield from flatten(el)
        else:
            yield el

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
        self.call_args = None
        self.error = None
        self.return_code = None
        super(Answer, self).__init__(*args, **kw)
        self.acquire()

    def acquire(self, timeout=-1):
        "Try to lock the answer to fill it up or use it."
        return self._lock.acquire(timeout=timeout)

    def release(self):
        "Release the lock, usually done by server thread."
        if self._lock.locked():
            self._lock.release()
        else:
            print('Warning: trying to release an unlocked mutex')

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

    Wrapper preserve as much as posible the original TWS API, calls arguments,etc.

    - When receiving data, we can postprocess data in wrapper internal structure,
      but the underlaying methods are called with unmodified values from network.

      >>> answer = app.reqManagedAccts()
      >>> answer
      ['DF1234567', 'DU1000000', 'DU1000001', 'DU1000002', 'DU1000003', 'DU1000003']

      despite original wrapper EWrapper.managerAccounts() if called with the string
      'DF1234567,DU1000000,DU1000001,DU1000002,DU1000003,DU1000003,'


    Notes:
    - BLOCKING requests are ended by TWS.
    - SUBSCRIPTION requests are ended by user, so the returned answer must be provided.

        >>> answer = app.reqRealTimeBars(instrument.contract, 5, 'TRADES', 0, [])
        >>> app.cancelRealTimeBars(answer)

    - ONESHOT request use a key group as internal identifier, as TWS will not require it.
       The key will remain in dwrapper as cache:

        >>> answer = app.reqManagedAccts()
        >>> answer.reqid
        'ManagedAccts'
        >>> app.dwrapper._req2data
        {'ManagedAccts': ['DF1234567', 'DU1000000', 'DU1000001', 'DU1000002', 'DU1000003', 'DU1000003']}


    """
    reqid = 0



    def __init__(self, app, excluded=('error', )):
        super(IBWrapper, self).__init__()
        self.app = app
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
        def gen_key(name):
            aliases = {
                'Accts':  'Accounts',
                # 'All': '(All)?',
            }
            aux = re.sub('(?!^)([A-Z][a-z]+)', r' \1', name).split()
            for i, key in enumerate(aux):
                aux[i] = aliases.get(key, key).lower()

            return '_'.join(aux)

        def split_names(name):
            aliases = {
                'Accts':  'Accounts',
                # 'All': '(All)?',
            }
            aux = re.sub('(?!^)([A-Z][a-z]+)', r' \1', name).split()
            aux = [list((k, )) for k in aux]

            for i, keys in enumerate(aux):
                # add aliases
                for j, key in enumerate(keys):
                    alias = aliases.get(key, None)
                    if alias:
                        keys.append(alias)

                # add plurals/singulars
                for j, key in enumerate(keys):
                    if key.endswith('s'):
                        keys.append(key[:-1])

                aux[i] = '|'.join(keys)

            return ''.join(['({})'.format(k) for k in aux])


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

        # debug = [k for k in available.keys()]

        debug = [

            # direct
            'error(reqId,errorCode,errorString)',

            # blocking
            'reqContractDetails(reqId,contract)',
            'contractDetails(reqId,contractDetails)',
            'contractDetailsEnd(reqId)',

            # subscription
            'reqRealTimeBars(reqId,contract,barSize,whatToShow,useRTH,realTimeBarsOptions)',
            'realtimeBar(reqId,time,open_,high,low,close,volume,wap,count)',
            'cancelRealTimeBars(reqId)',

            # one shot call
            'reqManagedAccts()',
            'managedAccounts(accountsList)',

            # subscription
            'reqHistoricalData(reqId,contract,endDateTime,durationStr,barSizeSetting,whatToShow,useRTH,formatDate,keepUpToDate,chartOptions)',
            'cancelHistoricalData(reqId)',
            'historicalData(reqId,bar)',
            'historicalDataEnd(reqId,start,end)',

            # Async requests without reqId
            'reqAllOpenOrders()',
            'reqOpenOrders()',
            'openOrder(orderId,contract,order,orderState)',
            'orderStatus(orderId,status,filled,remaining,avgFillPrice,permId,parentId,lastFillPrice,clientId,whyHeld,mktCapPrice)',
            'openOrderEnd()',

            '__init__(host,port,clientId)',
            'accountDownloadEnd(accountName)',
            'accountSummary(reqId,account,tag,value,currency)',
            'accountSummaryEnd(reqId)',
            'accountUpdateMulti(reqId,account,modelCode,key,value,currency)',
            'accountUpdateMultiEnd(reqId)',
            'bondContractDetails(reqId,contractDetails)',
            'calculateImpliedVolatility(reqId,contract,optionPrice,underPrice,implVolOptions)',
            'calculateOptionPrice(reqId,contract,volatility,underPrice,optPrcOptions)',
            'cancelAccountSummary(reqId)',
            'cancelAccountUpdatesMulti(reqId)',
            'cancelCalculateImpliedVolatility(reqId)',
            'cancelCalculateOptionPrice(reqId)',
            'cancelFundamentalData(reqId)',
            'cancelHeadTimeStamp(reqId)',
            'cancelHistogramData(tickerId)',
            'cancelMktData(reqId)',
            'cancelMktDepth(reqId,isSmartDepth)',
            'cancelNewsBulletins()',
            'cancelOrder(orderId)',
            'cancelPnL(reqId)',
            'cancelPnLSingle(reqId)',
            'cancelPositions()',
            'cancelPositionsMulti(reqId)',
            'cancelScannerSubscription(reqId)',
            'cancelTickByTickData(reqId)',
            'commissionReport(commissionReport)',
            'connect(host,port,clientId)',
            'connectAck()',
            'connectionClosed()',
            'currentTime(time)',
            'deltaNeutralValidation(reqId,deltaNeutralContract)',
            'disconnect()',
            'displayGroupList(reqId,groups)',
            'displayGroupUpdated(reqId,contractInfo)',
            'execDetails(reqId,contract,execution)',
            'execDetailsEnd(reqId)',
            'exerciseOptions(reqId,contract,exerciseAction,exerciseQuantity,account,override)',
            'familyCodes(familyCodes)',
            'fundamentalData(reqId,data)',
            'headTimestamp(reqId,headTimestamp)',
            'histogramData(reqId,items)',
            'historicalDataUpdate(reqId,bar)',
            'historicalNews(requestId,time,providerCode,articleId,headline)',
            'historicalNewsEnd(requestId,hasMore)',
            'historicalTicks(reqId,ticks,done)',
            'historicalTicksBidAsk(reqId,ticks,done)',
            'historicalTicksLast(reqId,ticks,done)',
            'isConnected()',
            'keyboardInterrupt()',
            'keyboardInterruptHard()',
            'logAnswer(fnName,fnParams)',
            'logRequest(fnName,fnParams)',
            'marketDataType(reqId,marketDataType)',
            'marketRule(marketRuleId,priceIncrements)',
            'mktDepthExchanges(depthMktDataDescriptions)',
            'newsArticle(requestId,articleType,articleText)',
            'newsProviders(newsProviders)',
            'nextValidId(orderId)',
            'orderBound(reqId,apiClientId,apiOrderId)',
            'placeOrder(orderId,contract,order)',
            'pnl(reqId,dailyPnL,unrealizedPnL,realizedPnL)',
            'pnlSingle(reqId,pos,dailyPnL,unrealizedPnL,realizedPnL,value)',
            'position(account,contract,position,avgCost)',
            'positionEnd()',
            'positionMulti(reqId,account,modelCode,contract,pos,avgCost)',
            'positionMultiEnd(reqId)',
            'queryDisplayGroups(reqId)',
            'receiveFA(faData,cxml)',
            'reconnect()',
            'replaceFA(faData,cxml)',
            'reqAccountSummary(reqId,groupName,tags)',
            'reqAccountUpdates(subscribe,acctCode)',
            'reqAccountUpdatesMulti(reqId,account,modelCode,ledgerAndNLV)',
            'reqAutoOpenOrders(bAutoBind)',
            'reqCurrentTime()',
            'reqExecutions(reqId,execFilter)',
            'reqFamilyCodes()',
            'reqFundamentalData(reqId,contract,reportType,fundamentalDataOptions)',
            'reqGlobalCancel()',
            'reqHeadTimeStamp(reqId,contract,whatToShow,useRTH,formatDate)',
            'reqHistogramData(tickerId,contract,useRTH,timePeriod)',
            'reqHistoricalNews(reqId,conId,providerCodes,startDateTime,endDateTime,totalResults,historicalNewsOptions)',
            'reqHistoricalTicks(reqId,contract,startDateTime,endDateTime,numberOfTicks,whatToShow,useRth,ignoreSize,miscOptions)',
            'reqIds(numIds)',
            'reqMarketDataType(marketDataType)',
            'reqMarketRule(marketRuleId)',
            'reqMatchingSymbols(reqId,pattern)',
            'reqMktData(reqId,contract,genericTickList,snapshot,regulatorySnapshot,mktDataOptions)',
            'reqMktDepth(reqId,contract,numRows,isSmartDepth,mktDepthOptions)',
            'reqMktDepthExchanges()',
            'reqNewsArticle(reqId,providerCode,articleId,newsArticleOptions)',
            'reqNewsBulletins(allMsgs)',
            'reqNewsProviders()',
            'reqPnL(reqId,account,modelCode)',
            'reqPnLSingle(reqId,account,modelCode,conid)',
            'reqPositions()',
            'reqPositionsMulti(reqId,account,modelCode)',
            'reqScannerParameters()',
            'reqScannerSubscription(reqId,subscription,scannerSubscriptionOptions,scannerSubscriptionFilterOptions)',
            'reqSecDefOptParams(reqId,underlyingSymbol,futFopExchange,underlyingSecType,underlyingConId)',
            'reqSmartComponents(reqId,bboExchange)',
            'reqSoftDollarTiers(reqId)',
            'reqTickByTickData(reqId,contract,tickType,numberOfTicks,ignoreSize)',
            'requestFA(faData)',
            'rerouteMktDataReq(reqId,conId,exchange)',
            'rerouteMktDepthReq(reqId,conId,exchange)',
            'reset()',
            'run()',
            'scannerData(reqId,rank,contractDetails,distance,benchmark,projection,legsStr)',
            'scannerDataEnd(reqId)',
            'scannerParameters(xml)',
            'securityDefinitionOptionParameter(reqId,exchange,underlyingConId,tradingClass,multiplier,expirations,strikes)',
            'securityDefinitionOptionParameterEnd(reqId)',
            'sendMsg(msg)',
            'serverVersion()',
            'setConnState(connState)',
            'setServerLogLevel(logLevel)',
            'smartComponents(reqId,smartComponentMap)',
            'softDollarTiers(reqId,tiers)',
            'start()',
            'startApi()',
            'stop()',
            'subscribeToGroupEvents(reqId,groupId)',
            'symbolSamples(reqId,contractDescriptions)',
            'tickByTickAllLast(reqId,tickType,time,price,size,tickAttribLast,exchange,specialConditions)',
            'tickByTickBidAsk(reqId,time,bidPrice,askPrice,bidSize,askSize,tickAttribBidAsk)',
            'tickByTickMidPoint(reqId,time,midPoint)',
            'tickEFP(reqId,tickType,basisPoints,formattedBasisPoints,totalDividends,holdDays,futureLastTradeDate,dividendImpact,dividendsToLastTradeDate)',
            'tickGeneric(reqId,tickType,value)',
            'tickNews(tickerId,timeStamp,providerCode,articleId,headline,extraData)',
            'tickOptionComputation(reqId,tickType,impliedVol,delta,optPrice,pvDividend,gamma,vega,theta,undPrice)',
            'tickPrice(reqId,tickType,price,attrib)',
            'tickReqParams(tickerId,minTick,bboExchange,snapshotPermissions)',
            'tickSize(reqId,tickType,size)',
            'tickSnapshotEnd(reqId)',
            'tickString(reqId,tickType,value)',
            'twsConnectionTime()',
            'unsubscribeFromGroupEvents(reqId)',
            'updateAccountTime(timeStamp)',
            'updateAccountValue(key,val,currency,accountName)',
            'updateDisplayGroup(reqId,contractInfo)',
            'updateMktDepth(reqId,position,operation,side,price,size)',
            'updateMktDepthL2(reqId,position,marketMaker,operation,side,price,size,isSmartDepth)',
            'updateNewsBulletin(msgId,msgType,newsMessage,originExch)',
            'updatePortfolio(contract,position,marketPrice,marketValue,averageCost,unrealizedPNL,realizedPNL,accountName)',
            'verifyAndAuthCompleted(isSuccessful,errorText)',
            'verifyAndAuthMessage(apiData,xyzResponse)',
            'verifyAndAuthMessageAPI(apiData,xyzChallange)',
            'verifyAndAuthRequest(apiName,apiVersion,opaqueIsvKey)',
            'verifyCompleted(isSuccessful,errorText)',
            'verifyMessage(apiData)',
            'verifyMessageAPI(apiData)',
            'verifyRequest(apiName,apiVersion)',
            'winError(text,lastError)'
            ]

        foo = dict()
        for k in debug:
            if k in available:
                foo[k] = available[k]

        available = foo

        BEHAVIOR = {
            'NOWRAP' : dict(),

            'BLOCK:REQ': dict(wrap=self.wrap_call, rid=True, timeout=60),
            'BLOCK:ANS': dict(wrap=self.wrap_receive, rid=True, release=False),
            'BLOCK:END': dict(wrap=self.wrap_ends, rid=True, release=True),

            'ASYNC:REQ': dict(wrap=self.wrap_call, rid=False, timeout=-1),
            'ASYNC:ANS': dict(wrap=self.wrap_receive, rid=False, release=True),
            'ASYNC:END': dict(wrap=self.wrap_end_subscription, rid=False, release=True),
            'SUBCR:END': dict(wrap=self.wrap_end_subscription, rid=False, release=True),

            'XUBCR:REQ': dict(wrap=self.wrap_call, rid=True, release=True, timeout=-1),
            'XUBCR:END': dict(wrap=self.wrap_end_subscription, rid=False, release=True),

            'ERROR' : dict(wrap=self.wrap_error),
        }

        patterns = [
            ('ERROR', r'(win)?error\(', []),
            ('NOWRAP', r'(.*connect.*|isConnected|twsConnectionTime)(.*)\(', []),
            ('NOWRAP', r'(setConnState|sendMsg|nextValidId)\(', []),
            ('NOWRAP', r'(keyboard|log|verify|serverVersion)(.*)\(', []),
            ('NOWRAP', r'__init__\(', []),
            ('NOWRAP', r'(reset|run|start|stop)(.*)\(', []),
            ('XUBCR:REQ', r'req(?P<key>.*realtimeBar.*)\((reqId|requestId)', []),
            ('BLOCK:REQ', r'req(?P<key>.*?)\((reqId|requestId)', []),
            ('BLOCK:REQ', r'placeOrder\((orderId)', []),
            ('BLOCK:REQ', r'calculate(?P<key>.*?)\((reqId|requestId)', []),
            ('BLOCK:END', r'(?P<key>.*?)End\((reqId|requestId)', []),
            ('SUBCR:END', r'cancel(?P<key>.*?)\((?!(reqId|requestId))', []),
            ('XUBCR:END', r'cancel(?P<key>.*?)\((reqId|requestId)', []),
            ('BLOCK:ANS', r'(?!(req))(?P<key>.*?)(?!(End))\((reqId|requestId)', []),
            ('ASYNC:REQ', r'req(?P<key>.*?)\((?!(reqId|requestId))', []),
            ('ASYNC:END', r'(?P<key>.*?)End\((?!(reqId|requestId))', []),
            ('ASYNC:ANS', r'(?!(req))(?P<key>.*?)(?!(End))\((?!(reqId|requestId))', [self._filter_string2list]),

        ]

        for i, (sig, method) in enumerate(available.items()):
            for ngroup, (group, pat, filters) in enumerate(patterns):
                context = dict(BEHAVIOR[group])
                context['filters'] = filters
                m = re.match(pat, sig, re.IGNORECASE)
                if m:
                    d = m.groupdict()
                    context.update(d)
                    key = d.get('key')
                    if key:
                        context['key'] = gen_key(key)
                    if 'reqManagedAccts'.lower() in sig.lower():
                        foo = 1
                    wrap = context.get('wrap')
                    if wrap:
                        print(' {} -> {} : <{}>'.format(sig, wrap.__name__, context.get('key')))
                        setattr(instance, method.__name__, wrap(method, **context))
                    else:
                        print('*** ignoring {} by pattern {}'.format(sig, pat))
                        foo = 1
                    break
            else:
                print("? {:3} {} doesn't match any rule".format(i, sig))
                foo = 1
        # Doubts:
        # cancelAccountUpdatesMulti: https://interactivebrokers.github.io/tws-api/classIBApi_1_1EClient.html#a7233f67c6c69f3057994b6b97a366dfb
        # cancelCalculateImpliedVolatility
        foo = 1

    def make_call(self, f, args, kw):
        "Prepare Answer placeholder and the key to analyze response history."

        context = self._wrapper_context[f]
        container = self.get_container(f)

        if context['rid']:  # synchronous request
            reqid = self.next_rid()
            args = tuple([reqid, *args])
        else:
            reqid = context['key']

        answer = container[reqid] = Answer()
        answer.call_args = (f, args, kw)
        answer.reqid = reqid
        tries = 10
        for tries in range(10):
            try:
                f(*args, **kw)
                return container, answer
            except OSError as why:
                if why.errno in (9, ):  # socket has been externally disconnected
                    self.app.stop()
                    time.sleep(1)  #  avoid too fast reconnecting dead lock
                    self.app.start()
                    foo = 1
                else:
                    raise why
        raise TimeoutError("Unable to reconnect to TWS")


    def wrap_call(self, f, **context):
        """Get a new request Id, prepare an answer to hold all partial data
        and make the underlaying API call.
        """
        def wrap(*args, **kw):
            # lapse = kw.pop('polling', -1)  # -1 will stop future calls
            # self.reschedule(f, -1, lapse, args, kw)
            aux = dict([(k, v) for (k, v) in kw.items() if k[0] != '_'])
            container, answer = self.make_call(f, args, aux)
            # handle blocking response until timeout
            context = self._wrapper_context[f]
            timeout = kw.get('_timeout', context['timeout'])
            if timeout > 0:
                if not answer.acquire(timeout):
                    raise TimeoutError("waiting finishing {}".format(f))

                if context['rid']:
                    container.pop(answer.reqid)
            return answer
        self._wrapper_context[f] = context
        return wrap

    def wrap_receive(self, f, **context):
        """Collect all the responses until request is completely finished."""
        def wrap(*args, **kw):
            context = self._wrapper_context[f]
            container = self.get_container(f)

            if context['rid']:
                reqid, _args = args[0], args[1:]
            else:
                reqid, _args = context['key'], args
                if reqid not in container:
                    answer = container[reqid] = Answer()
                    answer.call_args = (f, tuple('?', ), dict())
                    answer.reqid = reqid

            answer = container[reqid]
            for func in context['filters']:
                _args = func(_args)

            if len(_args) == 1:
                answer.append(*_args)
            else:
                answer.append(_args)

            if context['release']:
                answer.release()

            return f(*args, **kw)
        self._wrapper_context[f] = context
        return wrap

    def wrap_ends(self, f, **context):
        """Handle the end of a request
        - Release blocking thread that is waiting the response (if any).
        - Update differencial state for the key associated with the call.
        """
        def wrap(reqid, *args, **kw):
            self._cancel_request(f, reqid, *args, **kw)
            return f(reqid, *args, **kw)
        self._wrapper_context[f] = context
        return wrap

    def wrap_end_subscription(self, f, **context):
        def wrap(answer):
            reqid = answer.reqid
            self._cancel_request(f, reqid)
            return f(reqid)
        self._wrapper_context[f] = context
        return wrap

    def wrap_error(self, f, **context):
        def wrap(*args, **kw):
            # context = self._wrapper_context[f]
            container = self.get_container(f)

            print(args)
            reqid, _args = args[0], args[1:]
            answer = container.get(reqid)
            if answer is not None:
                answer.error = _args
                answer.release()

            return f(*args, **kw)
        self._wrapper_context[f] = context
        return wrap

    def _cancel_request(self, f, reqid, *args, **kw):
        container = self.get_container(f)
        answer = container.get(reqid)
        if answer is None:
            foo = 1  # request has been finalized earlier (e.g. historicalData)
        else:
            answer.return_code = (args, kw)
            answer.release()

    def _filter_string2list(self, args):
        result = list()
        for value in args:
            if isinstance(value, str):
                value = [v for v in value.strip().split(',') if v]
            result.append(value)
        return tuple(result)


class IBApp(EWrapper, EClient):
    """The base class for any IB application.
    It combines a running network client and a wrapper for receiving callbacks.
    """

    def __init__(self, host='tws', port=7496, clientId=0, demo=True):
        EWrapper.__init__(self)
        EClient.__init__(self, wrapper=self)

        self.demo = demo
        self.dwrapper = IBWrapper(app=self)
        self._thread = None
        self._connection_specs = (host, port, clientId)

    def start(self):
        "Connect and make the wrap, and start network main loop."
        self.reconnect()
        # need to be done after connection
        self.dwrapper.dinamic_wrapping(self)
        # TODO: it is safe to try to wrap multiples times?
        # self.dwrapper.dinamic_wrapping(self)
        self._thread = Thread(target=self.run)
        self._thread.start()

        time.sleep(1)  #  let the main loop to run, avoiding dead lock on fast reconnections

        if self.demo:
            answer = self.reqManagedAccts(_timeout=20)
            condition = [account.startswith('D') for account in flatten(answer)]
            if not reduce(and_, condition):
                raise RuntimeError(
                    'You can not operate with any demo account {}.\
                    Pass demo=False in constructor'.format(answer))

        foo = 1

    def stop(self):
        "Stop the network client"
        self.done = True
        if self.isConnected():
            self.disconnect()
        self._thread.join(timeout=10)
        self._thread = None
        foo = 1

    def reconnect(self):
        "Try to reconnect if is disconnected."
        while self._thread:
            print("waiting old thread to die")
            time.sleep(1)

        for tries in range(120):
            print("connecting to: {}".format(self._connection_specs))
            self.connect(*self._connection_specs)
            if self.isConnected():
                print("connected  to: {}".format(self._connection_specs))
                break
            print("Trying to connect to TWS ...[{}]".format(tries))
            time.sleep(1)
        else:
            raise RuntimeError("Unable to connect to TWS at {}".format(request.param))




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
