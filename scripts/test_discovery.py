"""Bounded streaming receipts for common test runners, independent of log tails."""
import re


class DiscoveryReceipt:
    def __init__(self):
        self.pending = b''
        self.observed = False
        self.maximum_count = 0
        self.maximum_skipped = 0
        self.go_passed = 0

    def line(self, value):
        counts = re.findall(rb'(?:\bRan\s+|\bTests run:\s*|#\s*tests\s+|\bTotal tests:\s*|\bcollected\s+)(\d+)(?:\s|$|,)', value, re.I)
        for pattern in (rb'\brunning\s+(\d+)\s+tests?\b', rb'\b(\d+) tests? completed\b', rb'\bout of (\d+)\b', rb'\btests\s+(\d+)\s*$'):
            counts += re.findall(pattern, value, re.I)
        if re.match(rb'^--- PASS:', value):
            self.go_passed += 1
            counts.append(str(self.go_passed).encode())
        skips = re.findall(rb'(?:\bskipped\s*[:=]\s*|#\s*skipped\s+)(\d+)', value, re.I)
        if skips:
            self.maximum_skipped = max(self.maximum_skipped, *(int(x) for x in skips))
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
        return {'observed': self.observed, 'maximum_count': max(0, self.maximum_count - self.maximum_skipped)}
