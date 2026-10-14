"""One owner of executing-runner verdicts; process failure is not product proof."""
import json
import re
import signal
import xml.etree.ElementTree as ET

ACCEPTANCE_PREFIX = 'LCSTATUS_ACCEPTANCE_RESULT '
INVOICE_ACCEPTANCE_CHECKS = frozenset({
    'consumer_import_pinned', 'entitlement_active', 'provider_discovered',
    'invoice_job_completed', 'declared_outputs_present', 'ledger_one_entry',
    'replay_no_duplicate', 'second_provider_refused', 'registration_removed',
})
INVOICE_PRODUCT_CHECKS = INVOICE_ACCEPTANCE_CHECKS - {'consumer_import_pinned', 'entitlement_active'}


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate proof key')
        result[key] = value
    return result


def invoice_acceptance_proof(stdout):
    try:
        proof = json.loads((stdout.strip().splitlines() or [''])[-1], object_pairs_hook=_object)
    except (ValueError, TypeError):
        return None
    if not isinstance(proof, dict) or type(proof.get('schema_version')) is not int or proof['schema_version'] != 1:
        return None
    checks = proof.get('checks')
    if (proof.get('all_checks') is not True or not isinstance(checks, dict)
            or set(checks) != INVOICE_ACCEPTANCE_CHECKS or any(v is not True for v in checks.values())):
        return None
    return proof


def product_failure_proof(runner, stdout):
    lines = [line for line in stdout.splitlines() if line.startswith(ACCEPTANCE_PREFIX)]
    if len(lines) != 1 or not stdout.rstrip().endswith(lines[0]):
        return None
    try:
        proof = json.loads(lines[0][len(ACCEPTANCE_PREFIX):], object_pairs_hook=_object)
    except (ValueError, TypeError):
        return None
    if (not isinstance(proof, dict) or set(proof) != {'schema_version', 'type', 'runner', 'checks', 'failed_checks'}
            or type(proof.get('schema_version')) is not int or proof['schema_version'] != 1
            or proof['type'] != 'product_failure' or proof['runner'] != runner):
        return None
    checks = proof['checks']
    if (not isinstance(checks, dict) or not checks
            or any(not isinstance(k, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', k)
                   or type(v) is not bool for k, v in checks.items())):
        return None
    failures = sorted(k for k, v in checks.items() if not v)
    if not failures or proof['failed_checks'] != failures:
        return None
    if runner == 'accept_ew_ip' and not set(failures) <= INVOICE_PRODUCT_CHECKS:
        return None
    return proof


def _junit(path):
    root = ET.parse(path).getroot()
    suites = [s for s in root.iter('testsuite') if not s.findall('.//testsuite')]
    if not suites:
        raise ValueError('JUnit has no test suite')
    total = [0, 0, 0, 0]
    for suite in suites:
        counts = [int(suite.get(k, '0')) for k in ('tests', 'failures', 'skipped', 'errors')]
        tests, failed, skipped, errors = counts
        if min(counts) < 0 or failed + skipped + errors > tests:
            raise ValueError('JUnit counts are inconsistent')
        cases = suite.findall('testcase')
        if cases and (len(cases) != tests or sum(c.find('failure') is not None for c in cases) != failed
                      or sum(c.find('error') is not None for c in cases) != errors
                      or sum(c.find('skipped') is not None for c in cases) != skipped):
            raise ValueError('JUnit cases disagree with counts')
        total = [a + b for a, b in zip(total, counts)]
    tests, failed, skipped, errors = total
    return tests - skipped - errors, failed, skipped, errors


def classify_execution(runner, *, phase='prepare', exit_code=None, reason=None, stdout='',
                       junit=None, summary=None, detail=None):
    """All four runners supply observations here, including prerequisite failures."""
    result = dict(verdict='unavailable', executed=None, failed=None, skipped=None,
                  summary=summary, detail={**dict(detail or {}), 'execution_phase': phase})
    def unavailable(why):
        result['detail']['harness_reason'] = why
        result['summary'] = 'harness fault: ' + why
        return result
    if reason is not None:
        return unavailable(reason)
    if type(exit_code) is not int:
        return unavailable(f'{phase}: execution did not complete')
    if exit_code < 0:
        try:
            name = signal.Signals(-exit_code).name
        except ValueError:
            name = str(-exit_code)
        return unavailable(f'{phase}: terminated by signal {name} (exit {exit_code})')
    if phase != 'test':
        return unavailable(f'{phase}: exited {exit_code} without product-test evidence')
    if runner == 'pytest':
        try:
            executed, failed, skipped, errors = _junit(junit)
        except (OSError, ValueError, TypeError, ET.ParseError):
            return unavailable(f'pytest: missing or invalid JUnit report (exit {exit_code})')
        result.update(executed=executed, failed=failed, skipped=skipped)
        result['detail']['junit_errors'] = errors
        if exit_code == 1 and failed >= 1:
            result['verdict'] = 'fail'
        elif exit_code != 0 or errors or failed:
            return unavailable(f'pytest: no completed counted-test verdict (exit {exit_code}; errors {errors})')
        else:
            result['verdict'] = 'pass' if executed else 'skip'
    elif runner == 'cargo_lib':
        lines = [line for line in stdout.splitlines() if line.startswith('test result:')]
        matches = [re.fullmatch(r'test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored; .*', line) for line in lines]
        if not matches or any(match is None for match in matches):
            return unavailable(f'cargo test: missing or invalid test summary (exit {exit_code})')
        passed = failed = skipped = 0
        for match in matches:
            status, p, f, s = match.groups()
            p, f, s = map(int, (p, f, s))
            if (status == 'ok') != (f == 0):
                return unavailable('cargo test: inconsistent test summary')
            passed += p;failed += f;skipped += s
        result.update(executed=passed + failed, failed=failed, skipped=skipped)
        result['summary'] = '; '.join(lines)
        # Cargo's ordinary failure status is 101; shell kill/interrupt statuses are not completion.
        if exit_code == 101 and failed >= 1:
            result['verdict'] = 'fail'
        elif exit_code != 0 or failed:
            return unavailable(f'cargo test: no completed counted-test verdict (exit {exit_code})')
        else:
            result['verdict'] = 'pass' if passed else 'skip'
    elif runner in ('accept_ew_ip', 'accept_ew_ds'):
        proof = product_failure_proof(runner, stdout)
        if exit_code == 1 and proof is not None:
            result.update(verdict='fail', executed=len(proof['checks']), failed=len(proof['failed_checks']))
            result['detail']['acceptance_failure_proof'] = proof
            result['summary'] = 'product checks failed: ' + ', '.join(proof['failed_checks'])
        elif exit_code == 0 and not any(l.startswith(ACCEPTANCE_PREFIX) for l in stdout.splitlines()):
            success = invoice_acceptance_proof(stdout) if runner == 'accept_ew_ip' else None
            if runner == 'accept_ew_ip' and success is None:
                return unavailable('acceptance process exited zero without complete proof')
            result.update(verdict='pass')
            if success is not None:
                result['detail']['acceptance_proof'] = success
        else:
            return unavailable(f'{runner}: missing or invalid product-failure proof (exit {exit_code})')
    else:
        raise ValueError(f'unsupported executing runner: {runner}')
    return result
