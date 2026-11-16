"""Small numeric checker for discussion: arithmetic and basic statistics on literal numbers.

Expressions are parsed to an AST and evaluated against a fixed function table. No names, attributes,
imports, strings-as-code, files or network: this checks numbers, it does not run programs.
"""
import ast
import math
import statistics as st


def _betacf(a, b, x):
    c, d = 1.0, 1.0 - (a + b) * x / (a + 1)
    d = 1 / (d if abs(d) > 1e-300 else 1e-300)
    h = d
    for m in range(1, 300):
        for num in (m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m)), -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1))):
            d = 1 + num * d
            d = 1 / (d if abs(d) > 1e-300 else 1e-300)
            c = 1 + num / (c if abs(c) > 1e-300 else 1e-300)
            h *= d * c
        if abs(d * c - 1) < 1e-14: break
    return h


def _betai(a, b, x):
    if x <= 0: return 0.0
    if x >= 1: return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x))
    return front * _betacf(a, b, x) / a if x < (a + 1) / (a + b + 2) else 1 - front * _betacf(b, a, 1 - x) / b


def t_p(t, df):
    """Two-sided p-value of Student's t."""
    return _betai(df / 2, .5, df / (df + t * t))


def norm_cdf(z):
    return .5 * math.erfc(-z / math.sqrt(2))


def _t_crit(df, conf=.95):
    low, high = 0.0, 1000.0
    for _ in range(200):
        mid = (low + high) / 2
        if t_p(mid, df) > 1 - conf: low = mid
        else: high = mid
    return (low + high) / 2


def _nums(values):
    values = list(values)
    if not values or len(values) > 100000 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in values):
        raise ValueError('Expected a non-empty list of numbers')
    return values


def describe(values):
    v = _nums(values); n = len(v)
    sd = st.stdev(v) if n > 1 else float('nan')
    q = st.quantiles(v, n=4, method='inclusive') if n > 1 else [v[0]] * 3
    return dict(n=n, mean=st.fmean(v), sd=sd, sem=sd / math.sqrt(n) if n > 1 else float('nan'), min=min(v), q1=q[0], median=q[1], q3=q[2], max=max(v))


def ci_mean(values, conf=.95):
    v = _nums(values); n = len(v)
    if n < 2: raise ValueError('Need at least two values')
    half = _t_crit(n - 1, conf) * st.stdev(v) / math.sqrt(n)
    m = st.fmean(v)
    return dict(mean=m, low=m - half, high=m + half, conf=conf, df=n - 1, method='t interval')


def ttest(a, b, paired=False):
    a, b = _nums(a), _nums(b)
    if paired:
        if len(a) != len(b) or len(a) < 2: raise ValueError('Paired test needs equal-length samples')
        d = [x - y for x, y in zip(a, b)]
        t = st.fmean(d) / (st.stdev(d) / math.sqrt(len(d))); df = len(d) - 1
        return dict(test='paired t', t=t, df=df, p_two_sided=t_p(t, df), mean_difference=st.fmean(d))
    if len(a) < 2 or len(b) < 2: raise ValueError('Each sample needs at least two values')
    va, vb = st.variance(a) / len(a), st.variance(b) / len(b)
    t = (st.fmean(a) - st.fmean(b)) / math.sqrt(va + vb)
    df = (va + vb) ** 2 / (va ** 2 / (len(a) - 1) + vb ** 2 / (len(b) - 1))
    pooled = math.sqrt(((len(a) - 1) * st.variance(a) + (len(b) - 1) * st.variance(b)) / (len(a) + len(b) - 2))
    return dict(test='Welch t', t=t, df=df, p_two_sided=t_p(t, df), mean_difference=st.fmean(a) - st.fmean(b),
                cohens_d=(st.fmean(a) - st.fmean(b)) / pooled if pooled else float('nan'))


def ttest_summary(m1, s1, n1, m2, s2, n2):
    """Welch t from summary statistics (mean, SD, n for each group)."""
    va, vb = s1 * s1 / n1, s2 * s2 / n2
    t = (m1 - m2) / math.sqrt(va + vb)
    df = (va + vb) ** 2 / (va ** 2 / (n1 - 1) + vb ** 2 / (n2 - 1))
    pooled = math.sqrt(((n1 - 1) * s1 * s1 + (n2 - 1) * s2 * s2) / (n1 + n2 - 2))
    return dict(test='Welch t (summary)', t=t, df=df, p_two_sided=t_p(t, df), mean_difference=m1 - m2, cohens_d=(m1 - m2) / pooled if pooled else float('nan'))


def pearson(x, y):
    x, y = _nums(x), _nums(y)
    if len(x) != len(y) or len(x) < 3: raise ValueError('Need two equal-length samples of at least three values')
    r = st.correlation(x, y); n = len(x)
    t = r * math.sqrt((n - 2) / max(1e-300, 1 - r * r))
    return dict(r=r, n=n, p_two_sided=t_p(t, n - 2), r_squared=r * r)


def linreg(x, y):
    x, y = _nums(x), _nums(y)
    fit = st.linear_regression(x, y)
    return dict(slope=fit.slope, intercept=fit.intercept, **pearson(x, y))


def proportion_ci(k, n, conf=.95):
    """Wilson interval for k successes out of n."""
    if not 0 <= k <= n or n <= 0: raise ValueError('Need 0 <= k <= n')
    low, high = 0.0, 10.0
    for _ in range(100):
        mid = (low + high) / 2
        if 2 * (1 - norm_cdf(mid)) > 1 - conf: low = mid
        else: high = mid
    z = (low + high) / 2; p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return dict(p=p, low=centre - half, high=centre + half, conf=conf, method='Wilson')


def chi2_2x2(a, b, c, d):
    n = a + b + c + d
    if min(a + b, c + d, a + c, b + d) <= 0: raise ValueError('Empty margin')
    chi = n * (a * d - b * c) ** 2 / ((a + b) * (c + d) * (a + c) * (b + d))
    result = dict(chi2=chi, df=1, p=math.erfc(math.sqrt(chi / 2)), note='no continuity correction')
    if b and c: result['odds_ratio'] = (a * d) / (b * c)
    if (a + b) and (c + d) and c: result['risk_ratio'] = (a / (a + b)) / (c / (c + d))
    return result


def z_p(z):
    return 2 * (1 - norm_cdf(abs(z)))


FUNCTIONS = {name: getattr(math, name) for name in ('sqrt', 'exp', 'log', 'log10', 'log2', 'sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'floor', 'ceil',
                                                    'factorial', 'comb', 'perm', 'erf', 'erfc', 'gamma', 'lgamma', 'degrees', 'radians', 'hypot')}
FUNCTIONS.update(abs=abs, round=round, min=min, max=max, sum=sum, len=len, mean=lambda v: st.fmean(_nums(v)), median=lambda v: st.median(_nums(v)),
                 sd=lambda v: st.stdev(_nums(v)), var=lambda v: st.variance(_nums(v)), sem=lambda v: st.stdev(_nums(v)) / math.sqrt(len(v)),
                 describe=describe, ci_mean=ci_mean, ttest=ttest, ttest_summary=ttest_summary, pearson=pearson, linreg=linreg,
                 proportion_ci=proportion_ci, chi2_2x2=chi2_2x2, t_p=t_p, z_p=z_p, norm_cdf=norm_cdf)
CONSTANTS = dict(pi=math.pi, e=math.e, True_=True)
OPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
       ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b}
HELP = ('Arithmetic (+ - * / // % **), lists like [1, 2, 3], and: ' + ', '.join(sorted(FUNCTIONS)) + '. Examples: '
        'ttest([5.1,4.9,5.6],[4.2,4.4,4.1]); ttest_summary(12.1,3.0,40,10.4,3.2,38); ci_mean([..]); proportion_ci(18,60); chi2_2x2(12,28,5,35); pearson(x,y).')


def _eval(node, depth=0):
    if depth > 40: raise ValueError('Expression too deeply nested')
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)): raise ValueError('Only numbers are allowed')
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        if len(node.elts) > 100000: raise ValueError('List too long')
        return [_eval(e, depth + 1) for e in node.elts]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _eval(node.operand, depth + 1)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left, depth + 1), _eval(node.right, depth + 1)
        if isinstance(left, list) or isinstance(right, list): raise ValueError('Arithmetic on lists is not supported; use a function')
        if isinstance(node.op, ast.Pow):
            if abs(right) > 1000 or (abs(left) > 1e6 and abs(right) > 50): raise ValueError('Exponent too large')
            return left ** right
        if type(node.op) in OPS: return OPS[type(node.op)](left, right)
    if isinstance(node, ast.Name) and node.id in CONSTANTS: return CONSTANTS[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS:
        kwargs = {k.arg: _eval(k.value, depth + 1) for k in node.keywords if k.arg in ('conf', 'paired')}
        if len(kwargs) != len(node.keywords): raise ValueError('Unknown keyword argument')
        return FUNCTIONS[node.func.id](*[_eval(a, depth + 1) for a in node.args], **kwargs)
    raise ValueError('Unsupported expression. ' + HELP)


def calculate(expression):
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 20000:
        raise ValueError('Supply one expression of at most 20000 characters')
    try:
        value = _eval(ast.parse(expression.strip(), mode='eval').body)
    except SyntaxError:
        raise ValueError('Not a valid expression. ' + HELP) from None
    except (ZeroDivisionError, OverflowError, st.StatisticsError, TypeError) as exc:
        raise ValueError(type(exc).__name__ + ': ' + str(exc)[:120]) from None

    def tidy(v):
        if isinstance(v, float): return float(f'{v:.10g}') if math.isfinite(v) else str(v)
        if isinstance(v, dict): return {k: tidy(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)): return [tidy(x) for x in v[:2000]]
        return v
    return dict(expression=expression.strip()[:500], result=tidy(value), note='Computed by the chat system from the numbers supplied; it does not validate where they came from.')
