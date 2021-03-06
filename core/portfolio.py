# ref: https://www.quantstart.com/articles/Event-Driven-Backtesting-with-Python-Part-V/

import datetime
import numpy as np
import pandas as pd
import queue
import copy

from abc import ABCMeta, abstractmethod
from math import floor

from .event import FillEvent, OrderEvent
from .performance import create_sharpe_ratio, create_drawdowns


class Portfolio(object):
    """
    The Portfolio class handles the positions and market
    value of all instruments at a resolution of a "bar",
    i.e. secondly, minutely, 5-min, 30-min, 60 min or EOD.
    """

    __metaclass__ = ABCMeta

    @abstractmethod
    def update_signal(self, event):
        """
        Acts on a SignalEvent to generate new orders
        based on the portfolio logic.
        """
        raise NotImplementedError("Should implement update_signal()")

    @abstractmethod
    def update_fill(self, event):
        """
        Updates the portfolio current positions and holdings
        from a FillEvent.
        """
        raise NotImplementedError("Should implement update_fill()")


class NaivePortfolio(Portfolio):
    """
    The NaivePortfolio object is designed to send orders to
    a brokerage object with a constant quantity size blindly,
    i.e. without any risk management or position sizing. It is
    used to test simpler strategies such as BuyAndHoldStrategy.
    """

    def __init__(self, bars, events, start_date, initial_capital=100000.0):
        """
        Initialises the portfolio with bars and an event queue.
        Also includes a starting datetime index and initial capital
        (USD unless otherwise stated).

        Parameters:
        bars - The DataHandler object with current market data.
        events - The Event Queue object.
        start_date - The start date (bar) of the portfolio.
        initial_capital - The starting capital in USD.
        """
        self.bars = bars
        self.events = events
        self.symbol_list = self.bars.symbol_list
        self.start_date = start_date
        self.initial_capital = initial_capital

        self.all_positions = self.construct_all_positions()
        self.current_positions = dict( (k,v) for k, v in [(s, 0) for s in self.symbol_list] )
        print(self.current_positions)
        print(self.all_positions)

        self.all_holdings = self.construct_all_holdings()
        self.current_holdings = self.construct_current_holdings()
        print(self.current_holdings)
        print(self.all_holdings)

        self.all_orders = {}
        self.all_fills = []

    def construct_all_positions(self):
        """
        Constructs the positions list using the start_date
        to determine when the time index will begin.
        """
        #  simply creates a dictionary for each symbol, sets the value to zero
        #  for each and then adds a datetime key, finally adding it to a list
        d = dict( (k,v) for k, v in [(s, 0) for s in self.symbol_list] )
        d['datetime'] = self.start_date
        return [d]

    def construct_all_holdings(self):
        """
        Constructs the holdings list using the start_date
        to determine when the time index will begin.
        """
        d = dict( (k,v) for k, v in [(s, 0.0) for s in self.symbol_list] )
        d['datetime'] = self.start_date
        d['cash'] = self.initial_capital
        d['commission'] = 0.0
        d['total'] = self.initial_capital
        return [d]

    def construct_current_holdings(self):
        """
        This constructs the dictionary which will hold the instantaneous
        value of the portfolio across all symbols.
        """
        d = dict( (k,v) for k, v in [(s, 0.0) for s in self.symbol_list] )
        d['cash'] = self.initial_capital
        d['commission'] = 0.0
        d['total'] = self.initial_capital
        return d

    def update_timeindex(self, event):
        """
        Adds a new record to the positions matrix for the current
        market data bar. This reflects the PREVIOUS bar, i.e. all
        current market data at this stage is known (OLHCVI).

        Makes use of a MarketEvent from the events queue.
        """

        latest_datetime = self.bars.get_latest_bar_datetime(
            self.symbol_list[0]
        )

        # Update positions
        # ===============
        dp = dict( (k,v) for k, v in [(s, 0) for s in self.symbol_list] )
        dp['datetime'] = latest_datetime

        for s in self.symbol_list:
            dp[s] = self.current_positions[s]

        # Append the current positions
        self.all_positions.append(dp)

        # Update holdings
        # ===============
        dh = dict( (k,v) for k, v in [(s, 0) for s in self.symbol_list] )
        dh['datetime'] = latest_datetime
        dh['cash'] = self.current_holdings['cash']
        dh['commission'] = self.current_holdings['commission']
        dh['total'] = self.current_holdings['cash']

        for s in self.symbol_list:
            # Approximation to the real value, [5] = close price
            # print(s)
            # print(bars[s][0][1].values[4])
            market_value = self.current_positions[s] * \
                self.bars.get_latest_bar_value(s, "close")
            dh[s] = market_value
            dh['total'] += market_value

        # Append the current holdings
        self.all_holdings.append(dh)

    def update_positions_from_fill(self, fill):
        """
        Takes a FilltEvent object and updates the position matrix
        to reflect the new position.

        Parameters:
        fill - The FillEvent object to update the positions with.
        """
        # Check whether the fill is a buy or sell
        fill_dir = 0
        if fill.direction == 'BUY':
            fill_dir = 1
        if fill.direction == 'SELL':
            fill_dir = -1

        # Update positions list with new quantities
        self.current_positions[fill.symbol] += fill_dir*fill.quantity

    def update_holdings_from_fill(self, fill):
        """
        Takes a FillEvent object and updates the holdings matrix
        to reflect the holdings value.

        Parameters:
        fill - The FillEvent object to update the holdings with.
        """
        # Check whether the fill is a buy or sell
        fill_dir = 0
        if fill.direction == 'BUY':
            fill_dir = 1
        if fill.direction == 'SELL':
            fill_dir = -1

        # Update holdings list with new quantities
        # This is estimated cause we do NOT know the cost of fill
        # in a simulated environment. (there are slippery etc in real.)
        fill_cost = self.bars.get_latest_bar_value(fill.symbol, "close")
        cost = fill_dir * fill_cost * fill.quantity
        self.current_holdings[fill.symbol] += cost
        self.current_holdings['commission'] += fill.commission
        self.current_holdings['cash'] -= (cost + fill.commission)
        self.current_holdings['total'] -= (cost + fill.commission)

    def update_orders_from_fill(self, fill):
        order = fill.order
        self.all_orders[order.order_id] = order

    def update_fill(self, event):
        """
        Updates the portfolio current positions and holdings
        from a FillEvent.
        """
        if event.type == 'FILL':
            self.update_positions_from_fill(event)
            self.update_holdings_from_fill(event)
            self.update_orders_from_fill(event)
            fill = vars(event)
            fill['order'] = event.order.order_id
            self.all_fills.append(fill)

    def update_fills(self, events):
        """
        Updates the portfolio current positions and holdings
        from a list of FillEvent.
        """
        for event in events:
            self.update_fill(event)

    def generate_naive_order(self, signal):
        """
        Simply transacts an OrderEvent object as a constant quantity
        sizing of the signal object, without risk management or
        position sizing considerations.

        Parameters:
        signal - The SignalEvent signal information.
        """
        order = None

        direction = signal.signal_type

        cur_quantity = self.current_positions[signal.symbol]

        if direction == 'LONG':
            # 新的多单
            order = OrderEvent(signal, signal.quantity, 'BUY')
        if direction == 'SHORT':
            # 新的空单
            order = OrderEvent(signal, signal.quantity, 'SELL')

        # EXIT 表示清空当前的多单或者空单
        if direction == 'EXIT' and cur_quantity > 0:
            order = OrderEvent(signal, abs(cur_quantity), 'SELL')
        if direction == 'EXIT' and cur_quantity < 0:
            order = OrderEvent(signal, abs(cur_quantity), 'BUY')
        return order

    def update_signal(self, event):
        """
        Acts on a SignalEvent to generate new orders
        based on the portfolio logic.
        """
        if event.type == 'SIGNAL':
            order_event = self.generate_naive_order(event)
            self.events.put(order_event)

    def create_equity_curve_dataframe(self):
        """
        Creates a pandas DataFrame from the all_holdings
        list of dictionaries.
        """
        curve = pd.DataFrame(self.all_holdings)
        curve.set_index('datetime', inplace=True)
        curve['returns'] = curve['total'].pct_change()
        curve['equity_curve'] = (1.0+curve['returns']).cumprod()
        self.equity_curve = curve

    def create_trade_history_dataframe(self):
        """
        Creates a pandas DataFrame from the all_positions
        list of dictionaries.
        """
        print(self.all_positions)
        trade = pd.DataFrame(self.all_positions)
        trade.set_index('datetime', inplace=True)
        self.trade_history = trade

    def create_order_history_dataframe(self):
        """
        Creates a pandas DataFrame from the all_positions
        list of dictionaries.
        """
        orders = pd.DataFrame([vars(c) for c in self.all_orders.values()])
        self.order_history = orders

    def output_summary_stats(self):
        """
        Creates a list of summary statistics for the portfolio such
        as Sharpe Ratio and drawdown information.
        """
        total_return = self.equity_curve['equity_curve'][-1]
        returns = self.equity_curve['returns']
        pnl = self.equity_curve['equity_curve']

        sharpe_ratio = create_sharpe_ratio(returns, periods=252*6)
        drawdown, max_dd, dd_duration = create_drawdowns(pnl)
        self.equity_curve['drawdown'] = drawdown

        profit = self.order_history['profit'].sum()
        profit = round(profit, 1)
        total_profit = sum(self.order_history[self.order_history['profit'] > 0].profit)
        total_profit = round(total_profit, 1)
        total_loss = sum(self.order_history[self.order_history['profit'] < 0].profit)
        total_loss = round(total_loss, 1)
        trade_no = len(self.order_history)
        winrate = round(len(self.order_history[self.order_history['profit'] > 0])/trade_no, 3)

        stats = [("Profit", "{} pips.".format(profit)),
                 ("Annualized Sharpe Ratio", "%0.1f" % sharpe_ratio),
                 ("Max Drawdown", "%0.1f%%" % (max_dd * 100.0)),
                 ("Drawdown Duration", "{} hours".format(dd_duration * 4)),
                 ("Win rate {}%".format(winrate * 100)),
                 ("Trade number {}".format(trade_no)),
                 ("Total Profit {}".format(total_profit)),
                 ("Total Loss {}".format(total_loss))]

        return stats
