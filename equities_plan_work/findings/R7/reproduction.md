# R7 reproduction (one screen, cold)

From repo root `/home/darren/Desktop/MCAS/`:

```bash
# 1. Confirm violation #19 in the gate source
grep -n "ELLIPSIS_OR_PASS_BODY" Backend/governance/kernel/ci_gates.py | head -3

# 2. Self-scan of the gate file (CLEAN due to FULLY_EXEMPT_FILES)
cd /home/darren/Desktop/MCAS/Backend
python3 -m governance.kernel.ci_gates governance/kernel/ci_gates.py

# 3. Full-repo scan (248 pre-existing violations; no regression)
python3 -m governance.kernel.ci_gates . | head -3

# 4. Seeded-fixture smoke test (should print 2 FIRE + 0 EXEMPT = exit 1)
mkdir -p /tmp/cigate_test && cat > /tmp/cigate_test/seeded.py << 'PY'
def foo():
    ...
def bar():
    pass
from abc import abstractmethod
class X:
    @abstractmethod
    def abstract_method(self):
        ...
def has_real_body():
    x = 1
    return x
PY
python3 -m governance.kernel.ci_gates /tmp/cigate_test/seeded.py ; echo "exit=$?"

# 5. Pytest (9/9 green)
python3 -m pytest tests/governance/test_ci_gates_violation_19.py -v
```

Expected:
- Step 2 -> `CI DOCTRINE GATES: CLEAN`
- Step 3 -> `CI DOCTRINE GATES: 248 VIOLATION(S) FOUND`
- Step 4 -> 2 hits on `foo` and `bar`, exit=1
- Step 5 -> 9 passed
