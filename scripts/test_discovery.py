"""Bounded streaming receipts for common test runners, independent of log tails."""
import re


class DiscoveryReceipt:
    def __init__(self):
        self.pending = b''
        self.observed = False
        self.maximum_count = 0

    def line(self, value):
        counts = re.findall(rb'(?:\bRan\s+|\bTests run:\s*|#\s*tests\s+|\bTotal tests:\s*|\bcollected\s+)(\d+)(?:\s|$|,)', value, re.I)
        if counts:
            self.observed = True
            self.maximum_count = max(self.maximum_count, *(int(value) for value in counts))
        if re.search(rb'\bno tests (?:found|ran|collected|were executed)\b', value, re.I):
            self.observed = True

    def feed(self, chunk):
        lines = (self.pending + chunk).split(b'\n')
        self.pending = lines.pop()[-8192:]
        for line in lines:
            self.line(line)

    def finish(self):
        self.line(self.pending)
        return {'observed': self.observed, 'maximum_count': self.maximum_count}
