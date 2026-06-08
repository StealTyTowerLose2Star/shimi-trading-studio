"""Quick unit test for us_doubler_predictor"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from haitao.us_doubler_predictor import (
    predict_doublers, predict_batch,
    _estimate_market_cap, _room_score, _size_boost
)

# Test room_score
r, d = _room_score(5.5)
print(f'room_score(5.5) = {r} | {d}')
assert r == 0.95, f"Expected 0.95, got {r}"

r, d = _room_score(250)
print(f'room_score(250) = {r} | {d}')
assert r == 0.25, f"Expected 0.25, got {r}"

r, d = _room_score(0)
print(f'room_score(0) = {r} | {d}')
assert r == 0.1, f"Expected 0.1, got {r}"

r, d = _room_score(750)
print(f'room_score(750) = {r} | {d}')
assert r == 0.1, f"Expected 0.1, got {r}"

# Test size_boost
print(f'size_boost(None) = {_size_boost(None)}')
print(f'size_boost(8) = {_size_boost(8)}')
print(f'size_boost(1000) = {_size_boost(1000)}')
print(f'size_boost(3) = {_size_boost(3)}')
assert _size_boost(None) == 1.0
assert _size_boost(3) == 2.0
assert _size_boost(1000) == 0.7
assert _size_boost(8) == 1.7

# Test predict_batch with empty list
result = predict_batch([])
print(f'predict_batch([]) = {result}')
assert result == []

# Test predict_doublers with empty list
result = predict_doublers([])
print(f'predict_doublers([]) = {result}')
assert result == {}

print("\nAll unit tests passed!")
