#!/usr/bin/env python3
from abc import ABC, abstractmethod
from re import split
from traceback import print_exc
from requests import get
from datetime import datetime, timedelta
from subprocess import check_call
from sys import stderr
from time import sleep

LEAP_FILE = "/data/leap-seconds.list"
LEAP_FILE_URL = "https://www.ietf.org/timezones/data/leap-seconds.list"
NTP_UTC_OFFSET = 2208988800
LEAP_FILE_RENEWAL_TIMEOUT = timedelta(days=60)

WAIT_TIME_CONFIGURATE = 15 * 60

def ntp2datetime(time):
    return datetime.utcfromtimestamp(time - NTP_UTC_OFFSET)

class LeapFile():
    def __init__(self, file, url, renewal_timeout):
        self.file = file
        self.url = url
        self.renewal_timeout = renewal_timeout
        self.expiry = None
        self.loaded = False

        self.time_map = {}
        self.times_sorted = []

    def reload(self):
        with open(self.file, "r") as fh:
            data = fh.read()
            self.parse(data)
        self.loaded = True

    def load(self):
        if self.loaded:
            return
        self.reload()

    def current_utc_tai_offset(self):
        self.load()
        now = datetime.utcnow()
        for time in self.times_sorted:
            if time <= now:
                return self.time_map[time]
        return 0

    def update(self, force=False):
        try:
            self.load()
        except FileNotFoundError:
            pass

        min_expiry = datetime.utcnow() - self.renewal_timeout
        if (not force) and self.expiry is not None and (self.expiry >= min_expiry):
            return

        res = get(self.url)
        res.raise_for_status()
        with open(self.file, "w") as fh:
            fh.write(res.text)

        if self.loaded:
            self.reload()

    def parse(self, data):
        self.time_map = {}
        self.times_sorted = []

        for line in data.split("\n"):
            line = line.strip()
            if len(line) < 1:
                continue
            if line[0] == "#":
                if len(line) < 2:
                    continue
                if line[1] == "@":
                    self.expiry = ntp2datetime(int(line[2:].strip(), 10))
            else:
                spl = split("\\s+", line)
                time = int(spl[0], 10)
                offset = int(spl[1], 10)
                self.time_map[ntp2datetime(time)] = offset

        self.times_sorted = sorted(self.time_map.keys(), reverse=True)

class Configuator(ABC):
    @abstractmethod
    def configure(self):
        pass

class PTP4LConfigurator(Configuator):
    def __init__(self, leapfile):
        self.leapfile = leapfile
        self.clock_class = 10
        self.clock_accuracy = 0xf23
        self.time_source = 0x20
        self.time_traceable = True
        self.frequency_traceable = False
        self.offset_scaled_log_variance = 0xFFFF

    def configure(self):
        check_call([
            "pmc", "-u", "-b", "0", "-f", "/etc/ptp4l.conf",
            f"""set GRANDMASTER_SETTINGS_NP
clockClass {self.clock_class}
clockAccuracy {self.clock_accuracy:#04x}
offsetScaledLogVariance {self.offset_scaled_log_variance:#04x}
currentUtcOffset {self.leapfile.current_utc_tai_offset()}
leap61 0
leap59 0
currentUtcOffsetValid 1
ptpTimescale 1
timeTraceable {int(self.time_traceable)}
frequencyTraceable {int(self.frequency_traceable)}
timeSource {self.time_source:#02x}
"""
        ])


class KernelConfigurator(Configuator):
    def __init__(self, leapfile):
        self.leapfile = leapfile

    def configure(self):
        offset = self.leapfile.current_utc_tai_offset()
        check_call(["set-tai", f"{offset}"])

def print_stderr(msg):
    stderr.write(f"{msg}\n")
    stderr.flush()

def main():
    leapfile = LeapFile(LEAP_FILE, LEAP_FILE_URL, LEAP_FILE_RENEWAL_TIMEOUT)

    configuators = [PTP4LConfigurator(leapfile), KernelConfigurator(leapfile)]

    while True:
        stderr.write("Running check loop...\n")
        stderr.flush()

        try:
            leapfile.update()
        except Exception:
            stderr.write("Error updating leapfile:\n")
            print_exc(file=stderr)
            stderr.flush()

        for configuator in configuators:
            try:
                configuator.configure()
            except Exception:
                stderr.write("Error running configuator:\n")
                print_exc(file=stderr)
                stderr.flush()

        stderr.write("Check loop complete!\n")
        stderr.flush()

        sleep(WAIT_TIME_CONFIGURATE)
        

if __name__ == "__main__":
    main()