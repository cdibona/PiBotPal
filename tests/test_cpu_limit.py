"""CPU policy validation tests use temporary files, never real sysfs writes."""
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('cpu_limit', Path(__file__).parents[1] / 'scripts/cpu-limit.py')
cpu_limit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cpu_limit)


def policy(root, name='policy0', minimum=1500000):
    path = root / name
    path.mkdir()
    for field, value in {'scaling_min_freq': str(minimum), 'cpuinfo_max_freq': '2400000',
                         'scaling_max_freq': '2400000', 'scaling_available_frequencies': '1500000 1800000 2400000'}.items():
        (path / field).write_text(value)
    return path


def test_applies_cap_to_every_policy_without_changing_minimum(tmp_path):
    paths = [policy(tmp_path, 'policy0'), policy(tmp_path, 'policy4')]
    assert cpu_limit.apply_limit(1500000, tmp_path) == 2
    assert all(int((p / 'scaling_max_freq').read_text()) == 1500000 for p in paths)
    assert all(int((p / 'scaling_min_freq').read_text()) == 1500000 for p in paths)


@pytest.mark.parametrize('value', [True, 1200000, 2500000, 1550000])
def test_invalid_frequency_never_changes_policy(tmp_path, value):
    path = policy(tmp_path)
    with pytest.raises(ValueError):
        cpu_limit.apply_limit(value, tmp_path)
    assert (path / 'scaling_max_freq').read_text() == '2400000'


def test_all_policies_validate_before_first_write(tmp_path):
    path = policy(tmp_path)
    policy(tmp_path, 'policy4', minimum=1800000)
    with pytest.raises(ValueError):
        cpu_limit.apply_limit(1500000, tmp_path)
    assert (path / 'scaling_max_freq').read_text() == '2400000'


def test_failed_write_restores_earlier_cap(tmp_path, monkeypatch):
    first, second = policy(tmp_path), policy(tmp_path, 'policy4')
    original = Path.write_text
    def write(path, value, *args, **kwargs):
        if path == second / 'scaling_max_freq' and value == '1500000\n':
            raise OSError('write failed')
        return original(path, value, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', write)
    with pytest.raises(OSError, match='write failed'):
        cpu_limit.apply_limit(1500000, tmp_path)
    assert (first / 'scaling_max_freq').read_text() == '2400000'
