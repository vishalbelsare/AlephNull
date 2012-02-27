import datetime
import quantoenv
import math
import pytz
import numpy as np
import numpy.linalg as la
from pymongo import ASCENDING, DESCENDING

class daily_return():
    
    def __init__(self, date, returns):
        self.date = date
        self.returns = returns
        
class periodmetrics():
    def __init__(self, start_date, end_date, returns, benchmark_returns):
        self.start_date = start_date
        self.end_date = end_date
        self.trading_calendar = trading_calendar
        self.algorithm_period_returns, self.algorithm_returns = self.calculate_period_returns(returns)
        self.benchmark_period_returns, self.benchmark_returns = self.calculate_period_returns(benchmark_returns)
        if(len(self.benchmark_returns) != len(self.algorithm_returns)):
            raise Exception("Mismatch between benchmark_returns ({bm_count}) and algorithm_returns ({algo_count}) in range {start} : {end}".format(
                                                                                                                     bm_count=len(self.benchmark_returns),
                                                                                                                     algo_count=len(self.algorithm_returns),
                                                                                                                     start=start_date, 
                                                                                                                     end=end_date))
        self.trading_days = len(self.benchmark_returns)
        self.benchmark_volatility = self.calculate_volatility(self.benchmark_returns)
        self.algorithm_volatility = self.calculate_volatility(self.algorithm_returns)
        self.treasury_period_return = self.choose_treasury()
        self.sharpe = self.calculate_sharpe()
        self.beta, self.algorithm_covariance, self.benchmark_variance, self.condition_number, self.eigen_values = self.calculate_beta()
        self.alpha = self.calculate_alpha()
        self.excess_return = self.algorithm_period_returns - self.treasury_period_return
        self.max_drawdown = self.calculate_max_drawdown()
        
    def __repr__(self):
        statements = []
        for metric in ["algorithm_period_returns", "benchmark_period_returns", "excess_return", "trading_days", "benchmark_volatility", "algorithm_volatility", "sharpe", "algorithm_covariance", "benchmark_variance", "beta", "alpha", "max_drawdown", "algorithm_returns", "benchmark_returns", "condition_number", "eigen_values"]:
            value = getattr(self, metric)
            statements.append("{metric}:{value}".format(metric=metric, value=value))
        
        return '\n'.join(statements)
        
    def calculate_period_returns(self, daily_returns):
        returns = [x.returns for x in daily_returns if x.date >= self.start_date and x.date <= self.end_date and self.trading_calendar.is_trading_day(x.date)]
        #quantoenv.qlogger.debug("using {count} daily returns out of {total}".format(count=len(returns),total=len(daily_returns)))
        period_returns = 1.0
        for r in returns:
            period_returns = period_returns * (1.0 + r)
        period_returns = period_returns - 1.0
        return period_returns, returns
        
    def calculate_volatility(self, daily_returns):
        #quantoenv.qlogger.debug("trading days {td}".format(td=self.trading_days))
        return np.std(daily_returns, ddof=1) * math.sqrt(self.trading_days)
        
    def calculate_sharpe(self):
        return (self.algorithm_period_returns - self.treasury_period_return) / self.algorithm_volatility
        
    def calculate_beta(self):
        #quantoenv.qlogger.debug("algorithm has {acount} days, benchmark has {bmcount} days".format(acount=len(self.algorithm_returns), bmcount=len(self.benchmark_returns)))
        #it doesn't make much sense to calculate beta for less than two days, so return none.
        if len(self.algorithm_returns) < 2:
            return 0.0, 0.0, 0.0, 0.0, []
        returns_matrix = np.vstack([self.algorithm_returns, self.benchmark_returns])
        C = np.cov(returns_matrix)
        eigen_values = la.eigvals(C)
        condition_number = max(eigen_values) / min(eigen_values)
        algorithm_covariance = C[0][1]
        benchmark_variance = C[1][1]
        beta = C[0][1] / C[1][1]
        #quantoenv.qlogger.debug("bm variance is {bmv}, returns matrix is {rm}, covariance is {c}, beta is {beta}".format(rm=returns_matrix, bmv=C[1][1], c=C, beta=beta))
        
        return beta, algorithm_covariance, benchmark_variance, condition_number, eigen_values
        
    def calculate_alpha(self):
        return self.algorithm_period_returns - (self.treasury_period_return + self.beta * (self.benchmark_period_returns - self.treasury_period_return))
        
    def calculate_max_drawdown(self):
        compounded_returns = []
        cur_return = 0.0
        for r in self.algorithm_returns:
            if(r != -1.0):
                cur_return = math.log(1.0 + r) + cur_return
            #this is a guard for a single day returning -100%
            else:
                quantoenv.qlogger.warn("negative 100 percent return, zeroing the returns")
                cur_return = 0.0
            compounded_returns.append(cur_return)
            
        #quantoenv.qlogger.debug("compounded returns are {cr}".format(cr=compounded_returns))
        cur_max = None
        max_drawdown = None
        for cur in compounded_returns:
            if cur_max == None or cur > cur_max:
                cur_max = cur
            
            drawdown = (cur - cur_max)
            if max_drawdown == None or drawdown < max_drawdown:
                max_drawdown = drawdown
        
        #quantoenv.qlogger.debug("max drawdown is: {dd}".format(dd=max_drawdown))
        if max_drawdown == None:
            return 0.0
            
        return 1.0 - math.exp(max_drawdown)
        
    
    def choose_treasury(self):
        td = self.end_date - self.start_date
        if td.days <= 31:
            self.treasury_duration = '1month'
        elif td.days <= 93:
            self.treasury_duration = '3month'
        elif td.days <= 186:
            self.treasury_duration = '6month'
        elif td.days <= 366:
            self.treasury_duration = '1year'
        elif td.days <= 365 * 2 + 1:
            self.treasury_duration = '2year'
        elif td.days <= 365 * 3 + 1:
            self.treasury_duration = '3year'
        elif td.days <= 365 * 5 + 2:
            self.treasury_duration = '5year'
        elif td.days <= 365 * 7 + 2:
            self.treasury_duration = '7year'
        elif td.days <= 365 * 10 + 2:
            self.treasury_duration = '10year'
        else:
            self.treasury_duration = '30year'
        
        treasuryQS = quantoenv.getTickDB().treasury_curves.find(
                                                            spec={"date" : {"$lte" : self.end_date}},
                                                            sort=[("date",DESCENDING)],
                                                            limit=3,
                                                            slave_ok=True)
    
        for curve in treasuryQS:
            self.treasury_curve = curve
            rate = self.treasury_curve[self.treasury_duration]
            #1month note data begins in 8/2001, so we can use 3month instead.
            if rate == None and self.treasury_duration == '1month':
                rate = self.treasury_curve['3month']
            if rate != None:
                return rate * (td.days + 1) / 365

        raise Exception("no rate for end date = {dt} and term = {term}, from {curve}. Using zero.".format(dt=self.end_date, 
                                                                                                          term=self.treasury_duration, 
                                                                                                          curve=self.treasury_curve['date']))
        
class riskmetrics():
    
    def __init__(self, algorithm_returns):
        """algorithm_returns needs to be a list of daily_return objects sorted in date ascending order"""
        self.db = quantoenv.getTickDB()
        self.algorithm_returns = algorithm_returns
        self.bm_returns = [x for x in benchmark_returns if x.date >= self.algorithm_returns[0].date and x.date <= self.algorithm_returns[-1].date]
        
        quantoenv.qlogger.debug("#### {start} thru {end} with {count} trading_days of {total} possible".format(start=self.algorithm_returns[0].date, 
                                                                                           end=self.algorithm_returns[-1].date,
                                                                                           count=len(self.bm_returns),
                                                                                           total=len(benchmark_returns)))
        
        #calculate month ends
        self.month_periods          = self.periodsInRange(1, self.algorithm_returns[0].date, self.algorithm_returns[-1].date)
        #calculate 3 month ends
        self.three_month_periods    = self.periodsInRange(3, self.algorithm_returns[0].date, self.algorithm_returns[-1].date)
        #calculate 6 month ends
        self.six_month_periods      = self.periodsInRange(6, self.algorithm_returns[0].date, self.algorithm_returns[-1].date)
        #calculate 1 year ends
        self.year_periods           = self.periodsInRange(12, self.algorithm_returns[0].date, self.algorithm_returns[-1].date)
        #calculate 3 year ends
        self.three_year_periods     = self.periodsInRange(36, self.algorithm_returns[0].date, self.algorithm_returns[-1].date)
        #calculate 5 year ends
        self.five_year_periods      = self.periodsInRange(60, self.algorithm_returns[0].date, self.algorithm_returns[-1].date)
        
        
    def periodsInRange(self, months_per, start, end):
        one_day = datetime.timedelta(days = 1)
        ends = []
        cur_start = start.replace(day=1)
        #ensure that we have an end at the end of a calendar month, in case the return series ends mid-month...
        the_end = advance_by_months(end.replace(day=1),1) - one_day
        while True:
            cur_end = advance_by_months(cur_start, months_per) - one_day
            if(cur_end > the_end):
                break
            #quantoenv.qlogger.debug("start: {start}, end: {end}".format(start=cur_start, end=cur_end))
            cur_period_metrics = periodmetrics(start_date=cur_start, end_date=cur_end, returns=self.algorithm_returns, benchmark_returns=self.bm_returns)
            ends.append(cur_period_metrics)
            cur_start = advance_by_months(cur_start, 1)
            
        return ends
        
    def store_to_db(self, back_test_run_id):
        col = quantoenv.getTickDB().risk_metrics
        for period in self.month_periods:
            for metric in ["algorithm_period_returns", "benchmark_period_returns", "excess_return", "trading_days", "benchmark_volatility", "algorithm_volatility", "sharpe", "beta", "alpha", "max_drawdown"]:
                record = {'back_test_run_id':back_test_run_id}
                record['ending_on']     = period.end_date
                record['metric_name']   = metric
                for dur in ["month", "three_month", "six_month", "year", "three_year", "five_year"]:
                    record[dur] = self.find_metric_by_end(period.end_date, dur, metric)
                    #quantoenv.qlogger.debug("storing {val} for {metric} and {dur}".format(val=record[dur], metric=metric, dur=dur))
                col.insert(record, safe=True)
    
    def find_metric_by_end(self, end_date, duration, metric):
        col = getattr(self, duration + "_periods")
        col = [getattr(x, metric) for x in col if x.end_date == end_date]
        if len(col) == 1:
            return col[0]
        return None
        
def advance_by_months(dt, jump_in_months):
    month = dt.month + jump_in_months
    years = month / 12
    month = month % 12 

    #no remainder means that we are landing in december.
    #modulo is, in a way, a zero indexed circular array. 
    #this is a way of converting to 1 indexed months. (in our modulo index, december is zeroth)
    if(month == 0):
        month = 12
        years = years - 1
    
    r = dt.replace(year = dt.year + years, month = month)
    return r

class TradingCalendar(object):

    def __init__(self, benchmark_returns):
        self.trading_days = []
        self.trading_day_map = {}
        for bm in benchmark_returns:
            self.trading_days.append(bm.date)
            self.trading_day_map[bm.date] = bm
    
    def normalize_date(self, test_date):
        return datetime.datetime(year=test_date.year, month=test_date.month, day=test_date.day, tzinfo=pytz.utc)
     
    def is_trading_day(self, test_date):
        dt = self.normalize_date(test_date)
        return self.trading_day_map.has_key(dt)
    
    def get_benchmark_daily_return(self, test_date):
        date = self.normalize_date(test_date)
        if self.trading_day_map.has_key(date):
            return self.trading_day_map[date].returns
        else:
            return 0.0

       
def get_benchmark_data():
    bmQS = quantoenv.getTickDB().bench_marks.find(
                                 spec={"symbol" : "GSPC", 
                                        "date":{"$gte": quantoenv.getUTCFromExchangeTime(datetime.datetime.strptime('01/01/1990','%m/%d/%Y')), 
                                                "$lte": quantoenv.getUTCFromExchangeTime(datetime.datetime.strptime('12/31/2010','%m/%d/%Y'))}},
                                 sort=[("date",ASCENDING)],
                                 slave_ok=True,
                                 as_class=quantoenv.DocWrap)
    bm_returns = []
    for bm in bmQS:
        bm_r = daily_return(date=bm.date.replace(tzinfo=pytz.utc), returns=bm.returns)
        bm_returns.append(bm_r)    

    cal = TradingCalendar(bm_returns)
    return bm_returns, cal

benchmark_returns, trading_calendar = get_benchmark_data() 
    