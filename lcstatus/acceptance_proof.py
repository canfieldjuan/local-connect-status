"""Observe native assertions while executing untouched product source as __main__."""
import ast
import builtins
import importlib.machinery
import json
import runpy
import sys
import traceback
import types
from pathlib import Path


def execute_script(script, runner, args, outcomes_path):
    rules = runpy.run_path(outcomes_path)
    script = Path(script).resolve()
    source = script.read_bytes()
    parsed = ast.parse(source, filename=str(script))
    hook = 'require' if runner == 'accept_ew_ip' else 'require_proof_checks'
    if not any(isinstance(n, ast.FunctionDef) and n.name == hook for n in parsed.body):
        print('acceptance harness: native assertion hook absent', file=sys.stderr)
        raise SystemExit(1)
    witness = []
    def observe(frame, event, arg):
        if (frame.f_code.co_filename != str(script) or frame.f_code.co_name != hook
                or frame.f_globals.get('__name__') != '__main__'):
            return None
        if event == 'exception':
            exception = arg[1]
            if runner == 'accept_ew_ip':
                name = frame.f_locals.get('name')
                if name in rules['INVOICE_PRODUCT_CHECKS'] and frame.f_locals.get('condition') is False:
                    checks = dict(frame.f_locals.get('checks', {}));checks[name] = False
                    witness.append((exception, checks))
            else:
                checks = frame.f_locals.get('checks')
                if isinstance(checks, dict) and checks and all(type(v) is bool for v in checks.values()) and False in checks.values():
                    witness.append((exception, dict(checks)))
        return observe
    previous_trace = sys.gettrace()
    main = types.ModuleType('__main__')
    main.__dict__.update(__file__=str(script), __package__=None, __spec__=None,
                         __loader__=importlib.machinery.SourceFileLoader('__main__', str(script)),
                         __cached__=None, __builtins__=builtins, __annotations__={})
    sys.modules['__main__'] = main
    sys.argv = [str(script), *args]
    if not sys.flags.safe_path:
        sys.path[0] = str(script.parent)
    sys.settrace(observe)
    try:
        exec(compile(parsed, str(script), 'exec'), main.__dict__)
    except BaseException as exc:
        matching = [checks for error, checks in witness if error is exc]
        if len(matching) == 1:
            checks = matching[0]
            receipt = dict(schema_version=1, type='product_failure', runner=runner,
                           checks=checks, failed_checks=sorted(k for k,v in checks.items() if v is False))
            text = rules['ACCEPTANCE_PREFIX'] + json.dumps(receipt, sort_keys=True)
            if rules['product_failure_proof'](runner, text) is not None:
                traceback.print_exception(exc, file=sys.stderr)
                print(text, flush=True)
                raise SystemExit(1) from None
        raise
    finally:
        sys.settrace(previous_trace)
        # Like direct file execution, retain the product module/argv through atexit,
        # but remove the transient file metadata when its top-level execution ends.
        main.__dict__.pop('__file__', None)
        main.__dict__.pop('__cached__', None)


def bootstrap(script, runner):
    """A stdlib-only loader for the existing product venv; no LCS install is needed."""
    here = Path(__file__).resolve()
    return (f"import runpy, sys; runpy.run_path({str(here)!r})['execute_script']("
            f"{str(script)!r}, {runner!r}, sys.argv[1:], {str(here.with_name('outcomes.py'))!r})")
