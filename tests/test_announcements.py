"""Queued arming speech uses mocked synthesis and PCM; no real audio devices."""
import threading
import time

import pytest

from pibotpal.media import Media


@pytest.fixture
def sound(tmp_path, monkeypatch):
    monkeypatch.setenv('PIBOTPAL_DATA', str(tmp_path))
    monkeypatch.setattr('pibotpal.camera.list_cameras', lambda: [])
    monkeypatch.setattr('pibotpal.audio.available', lambda: True)
    state = {'generated': [], 'played': [], 'opened': 0, 'maximum_open': 0,
             'capture': 0, 'stopped': 0, 'drained': 0, 'gate': {}, 'failure': set()}
    def synthesize(text):
        state['generated'].append(text)
        if text in state['gate']:
            started, release = state['gate'][text]
            started.set()
            if not release.wait(3):
                raise RuntimeError('Test synthesis did not release')
        if text in state['failure']:
            raise RuntimeError('Speech synthesis failed')
        return text.encode(), 16000, 1
    class Device:
        def __init__(self, *args, **kwargs):
            state['capture'] += int(kwargs.get('capture', False))
            state['opened'] += 1
            state['maximum_open'] = max(state['maximum_open'], state['opened'])
            self.cancel = threading.Event()
        def write(self, data):
            if self.cancel.is_set():
                return
            text = data.decode()
            state['played'].append(text)
            if state.get('play_gate'):
                started = state['play_gate']
                started.set()
                self.cancel.wait(3)
        def stop(self):
            state['stopped'] += 1
            self.cancel.set()
        def drain(self):
            if not self.cancel.is_set():
                state['drained'] += 1
                if state.get('drain_gate'):
                    started, release = state['drain_gate']
                    started.set()
                    while not self.cancel.is_set() and not release.wait(.005):
                        pass
        def close(self):
            self.cancel.set()
            state['opened'] -= 1
    monkeypatch.setattr('pibotpal.audio.speech_wave', synthesize)
    monkeypatch.setattr('pibotpal.audio.PCM', Device)
    monkeypatch.setattr('pibotpal.hat.enable_speaker', lambda: None)
    monkeypatch.setattr('pibotpal.hat.disable_speaker', lambda: None)
    media = Media()
    yield media, state
    for _, release in state['gate'].values():
        release.set()
    media.close()


def gate(state, text):
    started, release = threading.Event(), threading.Event()
    state['gate'][text] = started, release
    return started, release


def idle(media):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if not media.status()['speaker']['busy']:
            return
        time.sleep(.005)
    raise AssertionError('Audio worker did not finish')


def test_announcements_queue_after_speech_without_concurrent_pcm_or_microphone(sound):
    media, state = sound
    started, release = gate(state, 'Hello')
    media.speak('Hello')
    assert started.wait(1)
    media.announce('controller armed')
    media.announce('system armed')
    assert media.status()['speaker']['busy']
    assert media.status()['speaker']['queued_announcements'] == 2
    with pytest.raises(RuntimeError, match='busy'):
        media.speak('Another message')
    release.set(); idle(media)
    assert state['played'] == ['Hello', 'controller armed', 'system armed']
    assert state['maximum_open'] == 1 and state['opened'] == 0
    assert state['drained'] == 3
    assert state['capture'] == 0 and media.status()['microphone']['muted']


def test_toggle_bursts_coalesce_to_latest_pending_state_per_source(sound):
    media, state = sound
    started, release = gate(state, 'Hello')
    media.speak('Hello'); assert started.wait(1)
    for _ in range(50):
        media.announce('controller armed'); media.announce('system armed')
        media.announce('controller disarmed'); media.announce('system disarmed')
    assert media.status()['speaker']['queued_announcements'] == 2
    release.set(); idle(media)
    assert state['played'] == ['Hello', 'controller disarmed', 'system disarmed']


def test_stop_discards_old_synthesis_and_queue_without_cancelling_new_announcement(sound):
    media, state = sound
    started, release = gate(state, 'controller armed')
    media.announce('controller armed'); assert started.wait(1)
    media.announce('system armed')
    media.stop()
    assert media.status()['speaker']['queued_announcements'] == 0
    media.announce('system disarmed')
    release.set(); idle(media)
    assert state['played'] == ['system disarmed']
    assert state['maximum_open'] == 1 and state['capture'] == 0


def test_stop_cancels_active_pcm_and_queued_phrases(sound):
    media, state = sound
    state['play_gate'] = threading.Event()
    media.announce('controller armed')
    assert state['play_gate'].wait(1)
    media.announce('system armed')
    media.stop(); idle(media)
    assert state['stopped'] == 1 and state['opened'] == 0
    assert state['played'] == ['controller armed']
    assert media.status()['microphone']['muted']


def test_playback_waits_for_buffer_drain_and_stop_interrupts_it(sound):
    media, state = sound
    started, release = threading.Event(), threading.Event()
    state['drain_gate'] = started, release
    media.announce('controller armed')
    assert started.wait(1)
    assert media.status()['speaker']['busy'] and state['opened'] == 1
    media.stop(); idle(media)
    assert state['stopped'] == 1 and state['opened'] == 0


def test_synthesis_failure_is_reported_and_does_not_kill_queued_audio(sound):
    media, state = sound
    started, release = gate(state, 'controller armed')
    state['failure'].add('controller armed')
    media.announce('controller armed'); assert started.wait(1)
    media.announce('system armed'); release.set(); idle(media)
    assert state['played'] == ['system armed']
    assert media.status()['speaker']['error'] == 'Speech synthesis failed'
    assert state['opened'] == 0


def test_playback_open_failure_disables_amplifier_and_next_phrase_recovers(sound, monkeypatch):
    from pibotpal import audio
    media, state = sound
    started, release = gate(state, 'controller armed')
    original = audio.PCM
    opened = []
    amplifier = []
    def open_pcm(*args, **kwargs):
        opened.append(True)
        if len(opened) == 1:
            raise OSError('Speaker device unavailable')
        return original(*args, **kwargs)
    monkeypatch.setattr(audio, 'PCM', open_pcm)
    monkeypatch.setattr('pibotpal.hat.enable_speaker', lambda: amplifier.append(True))
    monkeypatch.setattr('pibotpal.hat.disable_speaker', lambda: amplifier.append(False))
    media.announce('controller armed'); assert started.wait(1)
    media.announce('system armed'); release.set(); idle(media)
    assert state['played'] == ['system armed']
    assert amplifier == [True, False, True, False]
    assert media.status()['speaker']['error'] == 'Speaker device unavailable'
    assert state['opened'] == 0


def test_closed_or_simulated_media_never_plays_announcements(sound, tmp_path):
    media, state = sound
    media.close()
    assert not media.announce('system armed')['queued']
    with pytest.raises(RuntimeError, match='closed'):
        media.speak('Hello')
    simulated = Media(simulate=True)
    assert not simulated.announce('system armed')['queued']
    assert state['generated'] == [] and state['capture'] == 0
    assert simulated.status()['microphone']['muted']


def test_invalid_announcement_cannot_enqueue_arbitrary_speech(sound):
    media, state = sound
    with pytest.raises(ValueError, match='Unknown arming'):
        media.announce('A different phrase')
    assert not media.status()['speaker']['busy'] and state['generated'] == []
