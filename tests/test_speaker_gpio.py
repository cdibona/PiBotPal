"""Speaker switching regressions; all GPIO and subprocess writes are mocked."""
import subprocess

import pytest

from pibotpal import hat


@pytest.fixture
def pi5_hat(monkeypatch, tmp_path):
    files = {
        '/proc/device-tree/compatible': b'raspberrypi,5-model-b\0brcm,bcm2712\0',
        '/proc/device-tree/axi/pcie@1000120000/rp1/gpio@d0000/rp1_i2s0_18_21/pins':
            b'gpio18\0gpio19\0gpio20\0gpio21\0',
        '/proc/asound/cards': b'2 [sndrpihifiberry]: RPi-simple - snd_rpi_hifiberry_dac\n',
    }
    for name, contents in files.items():
        path = tmp_path / name.lstrip('/')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    monkeypatch.setattr(hat, 'Path', lambda name: tmp_path / str(name).lstrip('/'))
    monkeypatch.setattr(hat, '_speaker', None)
    monkeypatch.delenv('PIBOTPAL_GPIO_CHIP', raising=False)
    monkeypatch.delenv('PIBOTPAL_SPEAKER_GPIO', raising=False)
    return tmp_path


def test_workaround_only_for_known_pi5_i2s_configuration(pi5_hat, monkeypatch):
    assert hat._speaker_needs_pinctrl(20)
    assert not hat._speaker_needs_pinctrl(12)
    monkeypatch.setenv('PIBOTPAL_GPIO_CHIP', '/dev/another-chip')
    assert not hat._speaker_needs_pinctrl(20)
    monkeypatch.delenv('PIBOTPAL_GPIO_CHIP')
    (pi5_hat / 'proc/device-tree/compatible').write_bytes(b'raspberrypi,4-model-b\0')
    assert not hat._speaker_needs_pinctrl(20)


@pytest.mark.parametrize('name,contents', [
    ('proc/asound/cards', b'USB audio'),
    ('proc/device-tree/axi/pcie@1000120000/rp1/gpio@d0000/rp1_i2s0_18_21/pins', b'gpio18\0gpio19\0gpio21\0'),
])
def test_missing_dac_or_unreserved_gpio_uses_normal_interface(pi5_hat, name, contents):
    (pi5_hat / name).write_bytes(contents)
    assert not hat._speaker_needs_pinctrl(20)
    (pi5_hat / name).unlink()
    assert not hat._speaker_needs_pinctrl(20)


def test_pinctrl_switches_only_amplifier_and_leaves_it_low(pi5_hat, monkeypatch):
    commands = []
    monkeypatch.setattr(subprocess, 'run', lambda command, **kwargs: commands.append((command, kwargs)))
    monkeypatch.setattr('periphery.GPIO', lambda *args, **kwargs: pytest.fail('Reserved I2S pin requested through GPIO'))
    hat.enable_speaker()
    hat.disable_speaker()
    hat.disable_speaker()
    assert [command for command, _ in commands] == [
        ['/usr/bin/pinctrl', 'set', '20', 'op', 'dl'],
        ['/usr/bin/pinctrl', 'set', '20', 'op', 'dh'],
        ['/usr/bin/pinctrl', 'set', '20', 'op', 'dl'],
    ]
    assert all(options == {'check': True, 'capture_output': True, 'text': True, 'timeout': 1}
               for _, options in commands)
    assert hat._speaker is None


@pytest.mark.parametrize('failed_level', ['dh', 'dl'])
def test_failed_enable_still_allows_amplifier_cleanup(pi5_hat, monkeypatch, failed_level):
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        if command[-1] == failed_level:
            raise subprocess.TimeoutExpired(command, 1)
    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(subprocess.TimeoutExpired):
        hat.enable_speaker()
    assert hat._speaker is not None
    monkeypatch.setattr(subprocess, 'run', lambda command, **kwargs: commands.append(command))
    hat.disable_speaker()
    assert commands[-1] == ['/usr/bin/pinctrl', 'set', '20', 'op', 'dl']
    assert hat._speaker is None


def test_other_speaker_pin_uses_gpio_handle(pi5_hat, monkeypatch):
    writes = []
    class GPIO:
        def __init__(self, chip, pin, direction, label):
            assert (chip, pin, direction, label) == ('/dev/fake', 12, 'low', 'pibotpal-speaker-enable')
        def write(self, enabled):
            writes.append(enabled)
        def close(self):
            writes.append('closed')
    monkeypatch.setenv('PIBOTPAL_SPEAKER_GPIO', '12')
    monkeypatch.setattr(hat, 'header_chip', lambda: '/dev/fake')
    monkeypatch.setattr('periphery.GPIO', GPIO)
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: pytest.fail('Unexpected pinctrl override'))
    hat.enable_speaker()
    hat.disable_speaker()
    assert writes == [True, False, 'closed']
