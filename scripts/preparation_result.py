"""Preparation status shared by project setup and Work baseline diagnostics."""
import re


def status(result):
    code = result['exit_code']
    if code == 0:
        return 'passed'
    if code in {2, 124, 126, 127}:
        return 'blocked'
    output = result.get('stdout_tail', '') + '\n' + result.get('stderr_tail', '')
    unavailable = (r'ModuleNotFoundError: No module named|ERR_MODULE_NOT_FOUND|'
                   r'Error: Cannot find module|Cannot find a Java installation|'
                   r'Could not resolve all (?:files|dependencies)|Could not transfer artifact|'
                   r'Plugin .* could not be resolved|Temporary failure in name resolution|'
                   r'Network is unreachable')
    if re.search(unavailable, output):
        result['reason'] = 'PREPARATION_DEPENDENCY_UNAVAILABLE'
        return 'blocked'
    return 'failed' if code == 1 else 'error'
