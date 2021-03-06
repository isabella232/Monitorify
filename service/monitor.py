"""
Classes used in Monitorify: MonitorService + MonitorTest
Author: Steffen Tiedemann Christensen <steffen@23company.com>
"""

import sys; sys.stdout = sys.stderr
import simplejson as json, time, datetime, urllib2, socket, traceback, sha, re, random, threading, thread, pycurl
from threading import Timer
import palb.core as palb
from bson.code import Code

class MonitorService:
    """ Class for handling each monitoring service """

    def __init__(self, config, service, db):
        # We shouldn't run all the tests at the exact same time, add some randomness to proceedings
        time.sleep(30*random.random())

        print "initing from %s" % service['url']

        # Store config a properties        
        self.config = config
        self.db = db
        self.collection = db['checks']
        self.url = service['url']
        self.key = service['key']
        self.interval = int(self.config['monitoring']['checkInterval'])

        # Prepare object for saving check data
        self.name = ""
        self.region = ""
        self.type = ""
        self.tests = {}
        self.time = int(time.time())
        self.status = 'loading'
        self.clearData()

        # Explicitly define a timeout on the socket
        socket.setdefaulttimeout(self.config['monitoring']['checkTimeout'])

        # Start running checks for this monitor
        self.run()
        
    def save(self):
        error = False
        for _ in self.data['tests']:
            if self.data['tests'][_]['error']: 
                error = True
            
        self.collection.insert({
                'key':self.key,
                'url':self.url,
                'time':datetime.datetime.utcfromtimestamp(self.time),
                'region':self.region,
                'type':self.type,
                'name':self.name,
                'error':error,
                'interval':self.interval,
                'status':self.status,
                'metrics':self.data['metrics'],
                'tests':self.data['tests']
                })
        self.clearData()
        return True

    def sign(self, url):
        ###print "Warning: Signing is disabled for now"
        return url
        timestamp = int(time.time())
        url = url + ('&' if re.search("\?", url) else '?') + "timestamp=" + str(timestamp)
        h = sha.new(self.key)
        h.update(url + str(timestamp))
        url = url + "&signature=" + h.hexdigest()
        return url

    def clearData(self):
        self.data = {'metrics':{}, 'tests':{}}

    def run(self):
        while 1:
            monitor_start_time = time.time()
            self.time = time.time()
            try: 
                # Request the URL and get data
                raw = ''
                signed_url = self.sign(self.url)
                req = urllib2.urlopen(signed_url)
                raw = req.read()
                del req

                try: 
                    # Parse JSON into a python dict
                    data = json.loads(raw)
                    try:
                        # Update information about the service
                        self.time = int(time.time())
                        self.name = data['serviceName']
                        self.type = data['serviceType']
                        self.region = data['serviceRegion']
                        # Remember metrics
                        self.data['metrics'] = data['metrics']
                        status = 'ok'

                        # Update tests
                        for test in data['tests']:
                            key = test['key']
                            if key in self.tests:
                                if self.tests[key].changed(test):
                                    # The test exists, but has changed
                                    # Stop the current test and overwrite with a new one
                                    del self.tests[key]
                                    self.tests[key] = MonitorTest(self.config, self, test, False)
                            else:
                                # The is a new test, set it up
                                self.tests[key] = MonitorTest(self.config, self, test)

                            # Trigger the tests
                            self.tests[key].run()
                                
                    except:
                        # The JSON document didn't meet our requirements
                        self.tests = {}
                        self.clearData()
                        traceback.print_exc()
                        status = 'invalid_content'
                except:
                    # The URL didn't return valid JSON
                    self.tests = {}
                    self.clearData()
                    status = 'invalid_json'
            except:
                # Couldn't access URL
                self.tests = {}
                self.clearData()
                status = 'invalid_url'
            
            # If status on the endpoint has changed, let's notify
            if self.status is not status:
                print "%s changed from %s to %s (region: %s, type: %s)" % (self.url, self.status, status, self.region, self.type)

            # Save status and return
            self.status = status
            self.save()

            # Do this again, later (with a bit of randomness +/- 5%)
            sleep_time = (self.interval - (time.time() - monitor_start_time)) * (0.95+(random.random()/10.0))
            if(sleep_time>0): time.sleep(sleep_time)


class MonitorTest:
    """ Class for tests """
    def __init__(self, config, service, test, new=True):
        # Store config a properties
        self.lastRunTime = None
        self.config = config
        self.service = service
        self.originalTest = test
        self.key = test['key']
        self.name = test['name']
        self.url = test['url']
        self.headers = test['headers'] if 'headers' in test else []
        self.interval = int(test['interval'])
        self.count = int(test['count'])
        self.concurrency = int(test['concurrency'])
        self.notifyOnError = test['notifyOnError']
        self.activeChecks = 0

        # We don't want unemployed threads
        if (self.concurrency>self.count): self.concurrency=self.count

        # We shouldn't run test more often than generic checks
        if (self.interval<self.config['monitoring']['checkInterval']): self.interval = self.config['monitoring']['checkInterval']

        if new:
            print "%s: Initiated test %s (key=%s, i=%s, n=%s, c=%s)" % (self.service.name, self.name, self.key, self.interval, self.count, self.concurrency)
        else:
            print "%s: Reinitiated test %s (key=%s, i=%s, n=%s, c=%s) since specs had changed" % (self.service.name, self.name, self.key, self.interval, self.count, self.concurrency)
        
    def check(self, args):
        while self.activeChecks>self.concurrency: 
            time.sleep(1)
        self.activeChecks += 1

        fp = open('/dev/null', "wb")
        c =  pycurl.Curl()
        c.setopt(pycurl.WRITEDATA, fp)
        c.setopt(pycurl.MAXCONNECTS, 1)
        c.setopt(pycurl.FRESH_CONNECT, 1)
        c.setopt(pycurl.URL, self.url)
        c.setopt(pycurl.HTTPHEADER, self.headers)
        try:
            c.perform()
        except:
            print "%s: Error checking url=%s" % (self.service.name, self.url)
            import traceback
            traceback.print_exc()
            self.activeChecks -= 1
            return None
        status = c.getinfo(pycurl.RESPONSE_CODE)
        size = c.getinfo(pycurl.SIZE_DOWNLOAD)
        t_total = c.getinfo(pycurl.TOTAL_TIME)
        t_connect = c.getinfo(pycurl.CONNECT_TIME)
        t_start = c.getinfo(pycurl.STARTTRANSFER_TIME)
        t_proc = t_total - t_connect
        self.activeChecks -= 1
        return palb.Result(t_total, size, status, detail_time=(t_connect, t_proc, t_start))

    def callback(self, result):
        self.stats.add(result)

    def getStats(self):
        stats = self.stats
        stats.stop()
        x = {'average_document_length':stats.avg_req_length, 'url':self.url, 'concurrency':self.concurrency, 'total_requests':self.count, 'total_time':stats.total_wall_time, 'completed_requests':len(stats.results), 'failed_requests':stats.failed_requests, 'total_transfered':stats.total_req_length, 'requests_per_second':len(stats.results)/stats.total_wall_time, 'time_per_request':stats.avg_req_time*1000, 'time_per_request_across_concurrent':stats.avg_req_time*1000/self.concurrency, 'transfer_rate':stats.total_req_length/stats.total_wall_time}
        
        connection_times = stats.connection_times()
        x['connection_times'] = {}
        if connection_times is not None:
            for name, data in zip(('connect', 'processing', 'waiting', 'total'), connection_times):
                x['connection_times'][name] = {}
                t_min, t_mean, t_sd, t_median, t_max = [v*1000 for v in data]
                t_min, t_mean, t_median, t_max = [round(v) for v in t_min, t_mean, t_median, t_max]
                x['connection_times'][name]['min'] = t_min
                x['connection_times'][name]['mean'] = t_mean
                x['connection_times'][name]['sd'] = t_sd
                x['connection_times'][name]['median'] = t_median
                x['connection_times'][name]['max'] = t_max

        x['time_distribution'] = {}
        for percent, seconds in stats.distribution():
            x['time_distribution'][str(percent)] = seconds*1024
        return x

    def run(self):
        if self.lastRunTime and time.time()-self.lastRunTime<self.interval:
            return
        self.lastRunTime = time.time()

        #print "%s: Running %s" % (self.service.name, self.name)
        try:
            # Run the test, please
            self.stats = palb.ResultStats()
            self.activeChecks = 0
            for _ in xrange(self.count):
                self.config['worker_threads'].queueTask(self.check, None, self.callback)
            while len(self.stats.results)<self.count:
                time.sleep(0.05)
            result = self.getStats()

            # Print information about the succesful test
            print "%s: %s/%s requests for %s succeeded in %.3f seconds" % (self.service.name, result['completed_requests'], result['total_requests'], self.name, result['total_time'])
        
            # Is this considered an error?
            result['error'] = True if float(self.config['monitoring']['errorWarnRatio'])*result['total_requests']>=result['completed_requests'] else False
        except:
            # Tetsts failed badly
            traceback.print_exc()
            try:
                print raw
            except:
                x = 1
            print "%s: Test %s failed totally and completely" % (self.service.name, self.name)
            result = {'url':self.url, 'concurrency':self.concurrency, 'total_requests':1, 'error':True}

        # Store tests as a metric
        self.service.data['tests'][self.key] = result

    def changed(self, test):
        # Check if the passed in test setup is different from the one set up with this object?
        return False if self.originalTest==test else True


class MonitorDenormalize:
    """ Class for denormalizing stuff within the service """
    
    def __init__(self, config, db):
        print "Starting denormalize service"

        # Store config a properties
        self.config = config
        self.db = db

        while 1:
            try:
                self.denormalize()
            except:
                import traceback
                traceback.print_exc()

            # Wait, then repeat
            time.sleep(180)
        
    def denormalize(self):
        print "Denorm: Beginning denormalization routine"
        
        # Get distinct values for region, type and name
        regions = self.db['checks'].distinct('region');
        types = self.db['checks'].distinct('type');
        names = self.db['checks'].distinct('name');

        # Get the first date in the list of checks
        map = Code("function() {emit('times', {min:this.time, max:this.time});}")
        reduce = Code("function(key,values) {"
                      "    res = values[0];"
                      "    values.forEach(function(value){"
                      "        res.min = Math.min(res.min, value.min);"
                      "        res.max = Math.max(res.max, value.max);"
                      "    });"
                      "    return(res);"
                      "}")
        result = self.db['checks'].map_reduce(map, reduce, "filterstime")
        row = self.db['filterstime'].find_one()
        min_time = int(row['value']['min'])
        max_time = int(row['value']['max'])
        print "Denorm: min_time=%s, max_time=%s" % (min_time, max_time)
        
        # Save stuff to the database
        self.db['filters'].update({}, {
                'regions':regions, 
                'types':types, 
                'names':names,
                'min_time':min_time,
                'max_time':max_time
                }, True);

        print "Denorm: Ending denormalization routine"
