"""Verify us_leveraged_scanner module"""
from haitao.us_leveraged_scanner import (
    _get_underlying, _compute_decay,
    analyze_leveraged, scan_leveraged_etfs, list_supported_etfs,
)

# Test _get_underlying for all mapped tickers
tests = {
    'TQQQ': ('QQQ', 3.0), 'SQQQ': ('QQQ', -3.0), 'QLD': ('QQQ', 2.0), 'PSQ': ('QQQ', -2.0),
    'SPXL': ('SPY', 3.0), 'SPXS': ('SPY', -3.0), 'SSO': ('SPY', 2.0), 'SDS': ('SPY', -2.0),
    'SOXL': ('SOXX', 3.0), 'SOXS': ('SOXX', -3.0),
    'LABU': ('XBI', 3.0), 'LABD': ('XBI', -3.0),
    'JNUG': ('GDX', 2.0), 'JDST': ('GDX', -2.0), 'NUGT': ('GDX', 2.0), 'DUST': ('GDX', -2.0),
    'DRIP': ('XLE', -3.0), 'ERX': ('XLE', 3.0), 'ERY': ('XLE', -3.0),
    'FAS': ('XLF', 3.0), 'FAZ': ('XLF', -3.0),
    'TNA': ('IWM', 3.0), 'TZA': ('IWM', -3.0), 'UDOW': ('IWM', 3.0), 'SDOW': ('IWM', -3.0),
}
pass_ = 0
fail_ = 0
for t, expected in tests.items():
    result = _get_underlying(t)
    if result == expected:
        pass_ += 1
    else:
        print(f'FAIL {t}: got {result}, expected {expected}')
        fail_ += 1
print(f'_get_underlying: {pass_}/{pass_ + fail_} passed')

# Test _get_underlying ValueError
try:
    _get_underlying('INVALID')
    print('FAIL: should have raised ValueError')
except ValueError:
    print('PASS: ValueError on unknown ticker')

# Test list_supported_etfs
m = list_supported_etfs()
print(f'list_supported_etfs: {m["total"]} ETFs in {m["underlying_groups"]} groups')

# Test _compute_decay with mock data
import pandas as pd
import numpy as np
np.random.seed(42)
df_etf = pd.DataFrame({'Close': 100 + np.cumsum(np.random.randn(30) * 0.5)})
df_und = pd.DataFrame({'Close': 100 + np.cumsum(np.random.randn(30) * 0.2)})

decay = _compute_decay('TQQQ', 'QQQ', df_etf, df_und)
required_keys = ['ticker', 'underlying', 'leverage', 'decay_pct', 'decay_warning',
                 'tracking_quality', 'etf_return_pct', 'underlying_return_pct',
                 'theoretical_return_pct', 'max_drawdown_pct', 'volatility_ratio']
missing = [k for k in required_keys if k not in decay]
if missing:
    print(f'FAIL: _compute_decay missing keys: {missing}')
else:
    print(f'PASS: _compute_decay returns all required keys')
    print(f'  decay_pct={decay["decay_pct"]}, tracking={decay["tracking_quality"]}')
    print(f'  etf_return={decay["etf_return_pct"]}%, underlying_return={decay["underlying_return_pct"]}%')
    print(f'  theoretical={decay["theoretical_return_pct"]}%, max_dd={decay["max_drawdown_pct"]}%')
    print(f'  vol_ratio={decay["volatility_ratio"]}')

# Test edge case: insufficient data
df_empty = pd.DataFrame({'Close': [100]})
decay_short = _compute_decay('TQQQ', 'QQQ', df_empty, df_und)
print(f'PASS: short data handled, error={decay_short.get("error")}')

print()
print('All verification passed!')
